from abc import ABC


class SetupException(Exception, ABC):
    reason_for_failure: str


class NoDiffsBeforeError(SetupException):
    reason_for_failure = "Couldn't get the diffs before the first commit"


class NoDiffsAfterError(SetupException):
    reason_for_failure = "Couldn't get the diffs after the last comment"


class NoLinesForCommentError(SetupException):
    reason_for_failure = "There are no line reference for the comment"


class CommentedFileNotInOriginalChanges(SetupException):
    reason_for_failure = (
        "Commented file is not part of the original PR (most like due to a merge of another branch)"
    )


class CantCloneRepoError(SetupException):
    reason_for_failure = "Couldn't clone the repository"


class CantEnsureFullHistoryError(SetupException):
    reason_for_failure = "Couldn't ensure the full history of the repo (fetch --unshallow)"


class CantFetchPRError(SetupException):
    reason_for_failure = "Couldn't fetch the PR's merge commit"


class CantCheckoutCommitError(SetupException):
    reason_for_failure = (
        "Coudln't checkout the PR's merge commit (even after fetching the pull/<pr_number>/merge)"
    )


class MultipleFilesError(SetupException):
    reason_for_failure = (
        "When requesting the contents of a file, a list of ContentFile was returned"
    )


class NotValidDirectory(SetupException):
    reason_for_failure = "The directory is not valid"


class CantFindBuildFile(SetupException):
    reason_for_failure = "Couldn't find the build file in the directory"
