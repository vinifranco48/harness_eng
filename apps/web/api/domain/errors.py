"""Errors shared by application services and HTTP handlers."""


class HarnessError(RuntimeError):
    pass


class ChatConflict(HarnessError):
    pass


class JobConflict(RuntimeError):
    pass


class JobInputError(ValueError):
    pass


class CliResolutionError(RuntimeError):
    pass
