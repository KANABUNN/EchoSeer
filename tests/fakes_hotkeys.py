"""Owned registrations plus independent reservations, without touching Windows."""
class FakeHotkeyBackend:
    def __init__(self):
        self.entries = {}
        self.reserved = set()
        self.calls = []

    def register(self, identifier, modifiers, key):
        chord = (modifiers & 7, key)
        self.calls.append(("register", identifier, modifiers, key))
        if chord in self.reserved or identifier in self.entries or chord in self.entries.values():
            return False
        self.entries[identifier] = chord
        return True

    def unregister(self, identifier):
        self.calls.append(("unregister", identifier))
        if identifier not in self.entries:
            return False
        self.entries.pop(identifier)
        return True
