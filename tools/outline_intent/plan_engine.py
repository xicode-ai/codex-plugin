"""Guarded execution of approved Outline intent plans."""
class OutlineIntentError(Exception):
    pass


def content_digest(plan):
    return ''


def prepare_plan(plan, repo_root, client, **kwargs):
    return {}


def apply_plan(plan, approved_digest, repo_root, client, **kwargs):
    return {}
