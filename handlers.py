from abc import ABC, abstractmethod
import os, re, docker, signal, javalang
from bs4 import BeautifulSoup
from typing import Iterable, Tuple, Iterator
import xml.etree.ElementTree as ET
from javalang.tree import PackageDeclaration

from errors import CantFindBuildFile, NotValidDirectory

REPORT_SIZE_THRESHOLD = 400   # less than 400 bytes (charcaters), we don't care about it


USER_ID = os.getuid()   # for container user
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
            command="tail -f /dev/null",  # to keep the container alive
            volumes={os.path.abspath(self.path): {"bind": "/repo", "mode": "rw"}},
            user=f"{USER_ID}:{GROUP_ID}",
            detach=True,
            tty=True,
        )

    def __exit__(self, *args):
        self.container.kill()
        self.container.remove()

    def check_for_tests(self) -> None:
        with open(os.path.join(self.path, self.build_file), "r") as f:
            content = f.read()

            for library in ["junit", "testng", "mockito"]:
                if library in content:
                    self.updates["detected_source_of_tests"] = library + " library in build file"
                    return

            for keyword in ["testImplementation", "functionalTests", "bwc_tests_enabled"]:
                if keyword in content:
                    self.updates["detected_source_of_tests"] = keyword + " keyword in build file"
                    return

        test_dirs = [
            "src/test/java",
            "src/test/kotlin",
            "src/test/groovy",
            "test",
        ]
        for td in test_dirs:
            if os.path.exists(os.path.join(self.path, td)):
                self.updates["detected_source_of_tests"] = td + " dir exists in repo"

        raise NoTestsFoundError("No tests found")

    def compile_repo(self) -> None:
        def timeout_handler(signum, frame):
            raise TimeoutError("Tests exceeded time limit")

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(3600)  # Set timeout to 1 hour (3600 seconds)

        try:
            exec_result = self.container.exec_run(self.compile_cmd())
            output = clean_output(exec_result.output)
            if exec_result.exit_code != 0:
                raise FailedToCompileError(output)
        except TimeoutError:
            self.updates["compiled_successfully"] = False
            self.updates[
                "error_msg"
            ] = "Compile process killed due to exceeding the 1-hour time limit"
        finally:
            signal.alarm(0)  # Cancel the alarm

    def test_repo(self) -> None:
        def timeout_handler(signum, frame):
            raise TimeoutError("Tests exceeded time limit")

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(3600)  # Set timeout to 1 hour (3600 seconds)

        try:
            exec_result = self.container.exec_run(self.test_cmd())
            output = clean_output(exec_result.output)
            if exec_result.exit_code != 0:
                raise FailedToTestError(output)

            self.extract_test_numbers(output)

        except TimeoutError:
            self.updates["tested_successfully"] = False
            self.updates["error_msg"] = "Test process killed due to exceeding the 1-hour time limit"
            return

        finally:
            signal.alarm(0)  # Cancel the alarm

    def generate_coverage_report(self, already_injected_manually: bool = False):
        result = self.container.exec_run(self.generate_coverage_report_cmd())
        if result.exit_code != 0:
            if already_injected_manually:
                raise CantExecJacoco(clean_output(result.output))

            build_file_path = os.path.join(self.path, self.build_file)
            if not os.path.exists(build_file_path):
                raise CantInjectJacoco("pom.xml not found")
            with open(build_file_path, "r") as f:
                og_content = f.read()
            try:
                self._try_to_inject_jacoco(build_file_path)
                self.generate_coverage_report(already_injected_manually=True)
            except (CantInjectJacoco, CantExecJacoco) as e:
                with open(build_file_path, "w") as f:
                    f.write(og_content)
                    raise e

    @abstractmethod
    def _try_to_inject_jacoco(self, build_file_path: str) -> None:
        pass

    def check_coverage(self, filename: str) -> Iterator[Tuple[str, float]]:
        """
        Check if the given filename is covered by JaCoCo.
        """
        found_at_least_one = False
        candidates = []
        for coverage_report_path in self.get_jacoco_report_paths():
            if not os.path.exists(coverage_report_path):
                raise NoCoverageReportFound(
                    f"Coverage report file '{coverage_report_path}' does not exist"
                )

            fully_qualified_class = self._extract_fully_qualified_class(filename)
            candidates.append({"report_file": coverage_report_path, "fqc": fully_qualified_class})
            # if coverage_report_path[:len(src_dir)] != src_dir:
            #     continue
            coverage = get_coverage_for_file(
                coverage_report_path, fully_qualified_class, os.path.basename(filename)
            )
            if coverage != -1:
                found_at_least_one = True
                yield coverage_report_path, coverage

        if not found_at_least_one:
            raise FileNotCovered(
                f"File '{filename}' didn't have any coverage in any of the jacoco reports: {candidates}"
            )

    def _extract_fully_qualified_class(self, filepath: str) -> str:
        if not filepath.endswith('.java'):
            raise NotJavaFileError(f"File '{filepath}' does not end with .java")

        if not os.path.exists(os.path.join(self.path, filepath)):
            raise FileNotFoundInRepoError(f"File '{filepath}' not found in repo")

        with open(os.path.join(self.path, filepath)) as f:
            try:
                parsed_tree = javalang.parse.parse(f.read())
            except javalang.parser.JavaSyntaxError as e:
                raise NotJavaFileError(
                    f"File '{filepath}' has a syntax error and could not be parsed by javalang, raised error: '{e}'"
                )

            package_name = None
            for _, node in parsed_tree.filter(PackageDeclaration):
                package_name = node.name   # type: ignore
                break  # Stop after finding the first package declaration

            if package_name is None:
                raise NoPackageFoundError(
                    f"File '{filepath}' did not have a packaged name recognized by javalang"
                )

            fully_qualified_class = package_name.replace('.', '/')
            # src_dir = filepath[:filepath.index(fully_qualified_class)]
            fully_qualified_class += "/" + os.path.basename(filepath)[:-5]   # -5 to remove '.java'
            return fully_qualified_class

    def clean_repo(self) -> None:
        self.container.exec_run(self.clean_cmd())

    @abstractmethod
    def get_type(self) -> str:
        pass

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
    def generate_coverage_report_cmd(self) -> str:
        pass

    @abstractmethod
    def get_jacoco_report_paths(self) -> Iterable[str]:
        pass

    @abstractmethod
    def container_name(self) -> str:
        pass


class MavenHandler(BuildHandler):
    def __init__(self, repo_path: str, build_file: str, updates: dict = {}) -> None:
        super().__init__(repo_path, build_file, updates)
        self.base_cmd = "mvn -B -Dstyle.color=never -Dartifact.download.skip=true"
        # -B (Batch Mode): Runs Maven in non-interactive mode, reducing output and removing download progress bars.
        # -Dstyle.color=never: Disables ANSI colors.
        # -Dartifact.download.skip=true: Prevents Maven from printing download logs (but still downloads dependencies when needed).

    def get_type(self) -> str:
        return "maven"

    def compile_cmd(self) -> str:
        return f"{self.base_cmd} clean compile"

    def test_cmd(self) -> str:
        return f"{self.base_cmd} test"

    def clean_cmd(self) -> str:
        return f"{self.base_cmd} clean"

    def generate_coverage_report_cmd(self):
        return f"{self.base_cmd} jacoco:report-aggregate"

    def container_name(self) -> str:
        return "crab-maven"

    def extract_test_numbers(self, output: str) -> None:
        pattern = r"\[INFO\] Results:\n\[INFO\]\s*\n\[INFO\] Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)"

        matches = re.findall(pattern, output)

        self.updates["n_tests"] = 0
        self.updates["n_tests_passed"] = 0  # Passed tests = Tests run - (Failures + Errors)
        self.updates["n_tests_failed"] = 0
        self.updates["n_tests_errors"] = 0
        self.updates["n_tests_skipped"] = 0

        if len(matches) == 0:
            raise NoTestResultsToExtractError("No test results found in Maven output:\n" + output)

        for match in matches:
            tests_run, failures, errors, skipped = map(int, match)
            self.updates["n_tests"] += tests_run
            self.updates["n_tests_failed"] += failures
            self.updates["n_tests_errors"] += errors
            self.updates["n_tests_skipped"] += skipped
            self.updates["n_tests_passed"] += tests_run - (
                failures + errors
            )  # Calculate passed tests

    def get_jacoco_report_paths(self) -> Iterable[str]:
        found_at_least_one = False
        for root, _, files in os.walk(os.path.join(self.path)):
            if "target/site" not in root:
                continue   # to avoid any misleading jacoco.xml randomly lying around
            for file in files:
                if file == "jacoco.xml":
                    found_at_least_one = True
                    yield os.path.join(root, file)
        if not found_at_least_one:
            raise NoCoverageReportFound(f"Couldn't find any 'jacoco.xml' in {self.path}")

    def _try_to_inject_jacoco(self, build_file_path: str) -> None:
        with open(build_file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if "<artifactId>jacoco-maven-plugin</artifactId>" in content:
            return   # already present

        jacoco_plugin = """
    <plugin>
        <groupId>org.jacoco</groupId>
        <artifactId>jacoco-maven-plugin</artifactId>
        <version>0.8.8</version>
        <executions>
            <execution>
                <goals>
                    <goal>prepare-agent</goal>
                </goals>
            </execution>
            <execution>
                <id>report</id>
                <phase>test</phase>
                <goals>
                    <goal>report</goal>
                </goals>
            </execution>
        </executions>
    </plugin>
"""

        if "<plugins>" in content:
            # just insert inside existing plugins
            content = content.replace("<plugins>", f"<plugins>\n{jacoco_plugin}")
        elif "</project>" in content:
            # plugins section doesn't exist, create full <build> section
            build_block = f"""
        <build>
            <plugins>
    {jacoco_plugin}
            </plugins>
        </build>
    """
            content = content.replace("</project>", f"{build_block}\n</project>")
        else:
            raise CantInjectJacoco("Could not find insertion point for plugins in pom.xml")

        with open(build_file_path, "w", encoding="utf-8") as f:
            f.write(content)


class GradleHandler(BuildHandler):
    def __init__(self, repo_path: str, build_file: str, updates: dict = {}) -> None:
        super().__init__(repo_path, build_file, updates)
        self.base_cmd = "gradle --no-daemon --console=plain"

    def get_type(self) -> str:
        return "gradle"

    def compile_cmd(self) -> str:
        return f"{self.base_cmd} compileJava"

    def test_cmd(self) -> str:
        return f"{self.base_cmd} test"

    def clean_cmd(self) -> str:
        return f"{self.base_cmd} clean"

    def generate_coverage_report_cmd(self) -> str:
        return f"{self.base_cmd} jacocoTestReport"

    def container_name(self) -> str:
        return "crab-gradle"

    def extract_test_numbers(self, output: str) -> None:
        self.updates["n_tests"] = -1
        self.updates["n_tests_passed"] = -1
        self.updates["n_tests_failed"] = -1
        self.updates["n_tests_errors"] = -1
        self.updates["n_tests_skipped"] = -1

        test_results_path = os.path.join(self.path, "build/reports/tests/test/index.html")
        if not os.path.exists(test_results_path):
            raise NoTestResultsToExtractError(
                "No test results found (prolly a repo with sub-projects)"
            )

        # Load the HTML file
        with open(test_results_path, "r") as file:
            soup = BeautifulSoup(file, "html.parser")

            # test_div = soup.select_one("div", class_="infoBox", id="tests")
            test_div = soup.select_one("div.infoBox#tests")
            if test_div is None:
                raise NoTestResultsToExtractError("No test results found (no div.infoBox#tests)")

            # counter_div = test_div.find("div", class_="counter")
            counter_div = test_div.select_one("div.counter")
            if counter_div is None:
                raise NoTestResultsToExtractError(
                    "No test results found (not div.counter for tests)"
                )

            self.updates["n_tests"] = int(counter_div.text.strip())

            # failures_div = soup.find("div", class_="infoBox", id="failures")
            failures_div = soup.select_one("div.infoBox#failures")
            if failures_div is None:
                raise NoTestResultsToExtractError("No test results found (no div.infoBox#failures)")

            # counter_div = failures_div.find("div", class_="counter")
            counter_div = failures_div.select_one("div.counter")
            if counter_div is None:
                raise NoTestResultsToExtractError(
                    "No test results found (not div.counter for failures)"
                )

            self.updates["n_tests_failed"] = int(counter_div.text.strip())

            # Calculate passed tests
            self.updates["n_tests_passed"] = (
                self.updates["n_tests"] - self.updates["n_tests_failed"]
            )

    def get_jacoco_report_paths(self) -> Iterable[str]:
        found_at_least_one = False
        for root, _, files in os.walk(os.path.join(self.path)):
            if "reports/jacoco" not in root:
                continue
            for file in files:
                if file == "index.html":
                    found_at_least_one = True
                    yield os.path.join(root, file)
        if not found_at_least_one:
            raise NoCoverageReportFound(
                f"Couldn't find any 'index.html' inside any 'reports/jacoco' in {self.path}"
            )

    def _try_to_inject_jacoco(self, build_file_path: str) -> None:
        with open(build_file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if "id 'jacoco'" in content or "apply plugin: 'jacoco'" in content:
            return  # already present

        jacoco_snippet = """
plugins {
    id 'jacoco'
}

jacoco {
    toolVersion = "0.8.8"
}

test {
    finalizedBy jacocoTestReport
}

jacocoTestReport {
    dependsOn test
    reports {
        xml.required = true
        html.required = true
    }
}"""

        content = jacoco_snippet + "\n\n" + content

        with open(build_file_path, "w", encoding="utf-8") as f:
            f.write(content)


class HandlerException(Exception, ABC):
    reason_for_failure = "Generic handler expection (this shouldn't appear)"


class NoTestsFoundError(HandlerException):
    reason_for_failure = "No tests found"


class FailedToCompileError(HandlerException):
    reason_for_failure = "Failed to compile"


class FailedToTestError(HandlerException):
    reason_for_failure = "Failed to test"


class NoTestResultsToExtractError(HandlerException):
    reason_for_failure = "Failed to extract test results"


class CantExecJacoco(HandlerException):
    reason_for_failure = "Couldn't execute jacoco"


class CantInjectJacoco(HandlerException):
    reason_for_failure = "Couldn't inject jacoco in the build file"


class NoCoverageReportFound(HandlerException):
    reason_for_failure = "No coverage report was found"


class FileNotCovered(HandlerException):
    reason_for_failure = "Commented file from the PR was not covered"


class GradleAggregateReportNotFound(HandlerException):
    reason_for_failure = "Couldn't find the aggregate report (with gradle it's messy)"


class NotJavaFileError(HandlerException):
    reason_for_failure = "File that was checked for coverage was not java file"


class NoPackageFoundError(HandlerException):
    reason_for_failure = "Java file did not contain a valid package name"


class FileNotFoundInRepoError(HandlerException):
    reason_for_failure = "Commented file not found in repo (likely renamed or deleted)"


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


def get_coverage_for_file(xml_file: str, target_fully_qualified_class: str, basename: str) -> float:
    # Parse the XML file
    tree = ET.parse(xml_file)
    root = tree.getroot()

    # Find coverage for the target file
    for package in root.findall(".//package"):
        for class_ in package.findall("class"):
            if (
                class_.get("sourcefilename") == basename
                and class_.get("name") == target_fully_qualified_class
            ):
                # Extract line coverage data
                line_counter = class_.find("counter[@type='LINE']")
                if line_counter is not None:
                    counter = line_counter.get("missed")
                    assert isinstance(counter, str)
                    missed = int(counter)
                    counter = line_counter.get("covered")
                    assert isinstance(counter, str)
                    covered = int(counter)
                    total = missed + covered
                    coverage = (covered / total) * 100 if total > 0 else 0
                    return coverage
    return -1


def get_build_handler(root: str, repo: str, verbose: bool = False) -> BuildHandler:
    """
    Get the path to the build file of a repository. The build file is either a
    `pom.xml`, `build.gradle`, or `build.xml` file.

    Args:
        root (str): The root directory in which the repository is located.
        repo (str): The name of the repository.

    Returns:
        str | None: The path to the repository if it is valid, `None` otherwise
    """
    path = os.path.join(root, repo)
    # Check if the given path is a directory
    if not os.path.isdir(path):
        raise NotValidDirectory(f"The path {path} is not a valid directory.")

    to_keep = ["pom.xml", "build.gradle"]
    for entry in os.scandir(path):
        if entry.is_file() and entry.name in to_keep:
            if verbose:
                print(f"Found {entry.name} in {repo} root, so keeping it and returning")
            if entry.name == "build.gradle":
                return GradleHandler(path, entry.name)
            else:
                return MavenHandler(path, entry.name)

    raise CantFindBuildFile(f"Couldn't find any of {to_keep} build files in the directory '{path}'")
    # # List files in the immediate subdirectories
    # for entry in os.scandir(path):
    #     if entry.is_dir():
    #         for sub_entry in os.scandir(entry.path):
    #             if sub_entry.is_file() and sub_entry.name in to_keep:
    #                 if verbose:
    #                     print(f"Found {sub_entry.name} in {repo} first level, so keeping it and returning")
    #                 updates["depth_of_build_file"] = 1
    #                 if entry.name == "build.gradle":
    #                     updates["build_system"] = "gradle"
    #                     return GradleHandler(path, os.path.join(entry.name, sub_entry.name), updates)
    #                 else:
    #                     updates["build_system"] = "maven"
    #                     return MavenHandler(path, os.path.join(entry.name, sub_entry.name), updates)

    # updates["error_msg"] = "No build file found"
    # return None
