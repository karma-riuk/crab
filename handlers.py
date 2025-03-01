from abc import ABC, abstractmethod
import os, re, docker, subprocess
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
        exec_result = self.container.exec_run(self.test_cmd())
        output = clean_output(exec_result.output)
        if exec_result.exit_code != 0:
            self.updates["tested_successfully"] = False
            self.updates["error_msg"] = output
            return False
        
        self.updates["tested_successfully"] = True
        self.updates["error_msg"] = output

        self.extract_test_numbers(output)

        grep_cmd = f"grep -r --include='*.java' -E '@Test|@ParameterizedTest' {self.path} | wc -l" # NOTE: After inspection, this is an upper bound, since comments get matched 
        try:
            result = subprocess.run(grep_cmd, shell=True, capture_output=True, text=True, check=True)
            test_count = result.stdout.strip()
        except subprocess.CalledProcessError as e:
            test_count = "-1"  # Default to 0 if grep command fails

        self.updates["n_tests_with_grep"] = test_count

        return True

    def clean_repo(self) -> None:
        self.container.exec_run(self.clean_cmd())


    @abstractmethod
    def compile_cmd(self) -> str:
        pass

    @abstractmethod
    def test_cmd(self) -> str:
        pass

    @abstractmethod
    def extract_test_numbers(self, output: str) -> None:
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

    def extract_test_numbers(self, output: str) -> None:
        # NOTE: I'ma afraid this might be specific for junit and wouldn't work for other testing frameworks
        pattern = r"\[INFO\] Results:\n\[INFO\]\s*\n\[INFO\] Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)"

        matches = re.findall(pattern, output)

        self.updates["n_tests"] = 0
        self.updates["n_tests_passed"] = 0  # Passed tests = Tests run - (Failures + Errors)
        self.updates["n_tests_failed"] = 0
        self.updates["n_tests_errors"] = 0
        self.updates["n_tests_skipped"] = 0

        for match in matches:
            tests_run, failures, errors, skipped = map(int, match)
            self.updates["n_tests"] += tests_run
            self.updates["n_tests_failed"] += failures
            self.updates["n_tests_errors"] += errors
            self.updates["n_tests_skipped"] += skipped
            self.updates["n_tests_passed"] += (tests_run - (failures + errors))  # Calculate passed tests

        

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

    def extract_test_numbers(self, output: str) -> None:
        self.updates["n_tests"] = -1
        self.updates["n_tests_passed"] = -1
        self.updates["n_tests_failed"] = -1
        self.updates["n_tests_errors"] = -1
        self.updates["n_tests_skipped"] = -1

        # Load the HTML file
        with open(os.path.join(self.path, "build/reports/tests/test/index.html"), "r", encoding="utf-8") as file:
            soup = BeautifulSoup(file, "html.parser")
        
            # Extract total tests
            self.updates["n_tests"] = int(soup.find("div", class_="infoBox", id="tests").find("div", class_="counter").text.strip())
            
            # Extract failed tests
            self.updates["n_tests_failed"] = int(soup.find("div", class_="infoBox", id="failures").find("div", class_="counter").text.strip())
            
            # Calculate passed tests
            self.updates["n_tests_passed"] = self.updates["n_tests"] - self.updates["n_tests_failed"]

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
