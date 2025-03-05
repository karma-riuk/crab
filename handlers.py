from abc import ABC, abstractmethod
import os, re, docker, signal
from bs4 import BeautifulSoup

USER_ID = os.getuid() # for container user
GROUP_ID = os.getgid() 

class BuildHandler(ABC):
    def __init__(self, repo_path: str, build_file: str, updates: dict) -> None:
        super().__init__()
        self.path: str = repo_path
        # self.container: Optional[Container] = None
        self.build_file: str = build_file
        self.updates = updates

    def set_client(self, client: docker.DockerClient):
        self.client = client

    def __enter__(self):
        self.container = self.client.containers.run(
            image=self.container_name(),
            command="tail -f /dev/null", # to keep the container alive
            volumes={os.path.abspath(self.path): {"bind": "/repo", "mode": "rw"}},
            user=f"{USER_ID}:{GROUP_ID}",
            detach=True,
            tty=True
        )

    def __exit__(self, *args):
        self.container.kill()
        self.container.remove()


    def has_tests(self) -> bool:
        with open(os.path.join(self.path, self.build_file), "r") as f:
            content = f.read()

            for library in ["junit", "testng", "mockito"]:
                if library in content:
                    self.updates["detected_source_of_tests"] = library + " library in build file"
                    return True

            for keyword in ["testImplementation", "functionalTests", "bwc_tests_enabled"]:
                if keyword in content:
                    self.updates["detected_source_of_tests"] = keyword + " keyword in build file"
                    return False

        test_dirs = [
            "src/test/java",
            "src/test/kotlin",
            "src/test/groovy",
            "test",
        ]
        for td in test_dirs:
            if os.path.exists(os.path.join(self.path, td)):
                self.updates["detected_source_of_tests"] = td + " dir exists in repo"
                return True

        self.updates["error_msg"] = "No tests found"
        return False

    def compile_repo(self) -> bool:
        exec_result = self.container.exec_run(self.compile_cmd())
        output = clean_output(exec_result.output)
        if exec_result.exit_code != 0:
            self.updates["compiled_successfully"] = False
            self.updates["error_msg"] = output
            return False
        
        self.updates["compiled_successfully"] = True 
        return True

    def test_repo(self) -> bool:
        def timeout_handler(signum, frame):
           raise TimeoutError("Tests exceeded time limit")

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(3600)  # Set timeout to 1 hour (3600 seconds)

        try:
            exec_result = self.container.exec_run(self.test_cmd())
            output = clean_output(exec_result.output)
            if exec_result.exit_code != 0:
                self.updates["tested_successfully"] = False
                self.updates["error_msg"] = output
                return False
            
            self.updates["tested_successfully"] = True
            self.updates["error_msg"] = output

            return self.extract_test_numbers(output)

        except TimeoutError:
            self.updates["tested_successfully"] = False
            self.updates["error_msg"] = "Test process killed due to exceeding the 1-hour time limit"
            return False

        finally:
            signal.alarm(0)  # Cancel the alarm

    def clean_repo(self) -> None:
        self.container.exec_run(self.clean_cmd())


    @abstractmethod
    def compile_cmd(self) -> str:
        pass

    @abstractmethod
    def test_cmd(self) -> str:
        pass

    @abstractmethod
    def extract_test_numbers(self, output: str) -> bool:
        pass

    @abstractmethod
    def clean_cmd(self) -> str:
        pass

    @abstractmethod
    def container_name(self) -> str:
        pass

class MavenHandler(BuildHandler):
    def __init__(self, repo_path: str, build_file: str, updates: dict) -> None:
        super().__init__(repo_path, build_file, updates)
        self.base_cmd = "mvn -B -Dstyle.color=never -Dartifact.download.skip=true"
        # -B (Batch Mode): Runs Maven in non-interactive mode, reducing output and removing download progress bars.
        # -Dstyle.color=never: Disables ANSI colors.
        # -Dartifact.download.skip=true: Prevents Maven from printing download logs (but still downloads dependencies when needed).

    def compile_cmd(self) -> str:
        return f"{self.base_cmd} clean compile"

    def test_cmd(self) -> str:
        return f"{self.base_cmd} test"

    def clean_cmd(self) -> str:
        return f"{self.base_cmd} clean"
    
    def container_name(self) -> str:
        return "crab-maven"

    def extract_test_numbers(self, output: str) -> bool:
        pattern = r"\[INFO\] Results:\n\[INFO\]\s*\n\[INFO\] Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)"

        matches = re.findall(pattern, output)

        self.updates["n_tests"] = 0
        self.updates["n_tests_passed"] = 0  # Passed tests = Tests run - (Failures + Errors)
        self.updates["n_tests_failed"] = 0
        self.updates["n_tests_errors"] = 0
        self.updates["n_tests_skipped"] = 0

        if len(matches) == 0:
            self.updates["error_msg"] = "No test results found in Maven output:\n" + output
            return False

        for match in matches:
            tests_run, failures, errors, skipped = map(int, match)
            self.updates["n_tests"] += tests_run
            self.updates["n_tests_failed"] += failures
            self.updates["n_tests_errors"] += errors
            self.updates["n_tests_skipped"] += skipped
            self.updates["n_tests_passed"] += (tests_run - (failures + errors))  # Calculate passed tests

        return True

        

class GradleHandler(BuildHandler):
    def __init__(self, repo_path: str, build_file: str, updates: dict) -> None:
        super().__init__(repo_path, build_file, updates)
        self.base_cmd = "gradle --no-daemon --console=plain"

    def compile_cmd(self) -> str:
        return f"{self.base_cmd} compileJava"

    def test_cmd(self) -> str:
        return f"{self.base_cmd} test"

    def clean_cmd(self) -> str:
        return f"{self.base_cmd} clean"
    
    def container_name(self) -> str:
        return "crab-gradle"

    def extract_test_numbers(self, output: str) -> bool:
        self.updates["n_tests"] = -1
        self.updates["n_tests_passed"] = -1
        self.updates["n_tests_failed"] = -1
        self.updates["n_tests_errors"] = -1
        self.updates["n_tests_skipped"] = -1

        test_results_path = os.path.join(self.path, "build/reports/tests/test/index.html")
        if not os.path.exists(test_results_path):
            self.updates["error_msg"] = "No test results found (prolly a repo with sub-projects)"
            return False

        # Load the HTML file
        with open(test_results_path, "r") as file:
            soup = BeautifulSoup(file, "html.parser")
        
            test_div = soup.find("div", class_="infoBox", id="tests")
            if test_div is None:
                self.updates["error_msg"] = "No test results found (no div.infoBox#tests)"
                return False

            counter_div = test_div.find("div", class_="counter")
            if counter_div is None:
                self.updates["error_msg"] = "No test results found (not div.counter for tests)"
                return False

            self.updates["n_tests"] = int(counter_div.text.strip())
            
            failures_div = soup.find("div", class_="infoBox", id="failures")
            if failures_div is None:
                self.updates["error_msg"] = "No test results found (no div.infoBox#failures)"
                return False

            counter_div = failures_div.find("div", class_="counter")
            if counter_div is None:
                self.updates["error_msg"] = "No test results found (not div.counter for failures)"
                return False

            self.updates["n_tests_failed"] = int(counter_div.text.strip())
            
            # Calculate passed tests
            self.updates["n_tests_passed"] = self.updates["n_tests"] - self.updates["n_tests_failed"]
        return True

def merge_download_lines(lines: list) -> list:
    """
    Merges lines that are part of the same download block in Maven output.

    Args:
        lines (list): The lines to merge.

    Returns:
        list: The merged lines.
    """
    downloading_block = False
    cleaned_lines = []
    for line in lines:
        if re.match(r"\[INFO\] Download(ing|ed) from", line):
            if not downloading_block:
                cleaned_lines.append("[CRAB] Downloading stuff")
                downloading_block = True
        else:
            cleaned_lines.append(line)
            downloading_block = False
    return cleaned_lines

def merge_unapproved_licences(lines: list) -> list:
    """
    Merges lines that are part of the same unapproved licences block in Maven output.

    Args:
        lines (list): The lines to merge.

    Returns:
        list: The merged lines.
    """
    licenses_block = False
    cleaned_lines = []
    for line in lines:
        if re.match(r"\[WARNING\] Files with unapproved licenses:", line):
            cleaned_lines.append(line)
            cleaned_lines.append("[CRAB] List of all the unapproved licenses...")
            licenses_block = True
        elif licenses_block and not re.match(r"\s+\?\/\.m2\/repository", line):
            licenses_block = False

        if not licenses_block:
            cleaned_lines.append(line)
    return cleaned_lines

def clean_output(output: bytes) -> str:
    output_lines = output.decode().split("\n")

    cleaned_lines = merge_download_lines(output_lines)
    cleaned_lines = merge_unapproved_licences(cleaned_lines)

    return "\n".join(cleaned_lines)
