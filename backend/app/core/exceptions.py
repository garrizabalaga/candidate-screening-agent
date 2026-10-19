"""Errores de negocio compartidos por varios servicios."""


class CandidateNotFound(Exception):
    pass


class RecruiterNotFound(Exception):
    pass
