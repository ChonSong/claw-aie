SANITISE_FIELDS = ["PASSWORD", "SECRET", "TOKEN", "KEY", "API_KEY", "AUTHORIZATION", "CREDENTIAL", "PRIVATE_KEY", "ACCESS_TOKEN"]

def sanitise(data):
    """Recursively sanitise dict/list/str. Returns new object, does not mutate."""
    if isinstance(data, dict):
        return {k: sanitise(v) if k.upper() not in SANITISE_FIELDS else "[REDACTED]" for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitise(item) for item in data]
    return data
