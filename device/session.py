from dataclasses import dataclass

from device.recorder import Recorder


@dataclass(frozen=True)
class Connection:
    recording_id: str
    generation: int


class SessionManager:
    def __init__(self, recorder: Recorder):
        self.recorder = recorder
        self._active: set[Connection] = set()
        self._accepting = True

    def set_accepting(self, accepting: bool) -> None:
        """Control whether the local device accepts new handshakes."""
        self._accepting = accepting

    def connect(self, recording_id: str, generation: int) -> Connection:
        if not self.recorder.has_recording(recording_id):
            raise KeyError(recording_id)
        if not self._accepting:
            raise ConnectionError('device refused the connection')
        connection = Connection(recording_id, generation)
        if connection in self._active:
            raise ValueError('device connection is already active')
        self._active.add(connection)
        return connection

    def terminate(self, connection: Connection) -> None:
        self._active.discard(connection)

    def is_active(self, connection: Connection) -> bool:
        return connection in self._active
