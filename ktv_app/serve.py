import os
import socket
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
load_dotenv(Path(__file__).with_name(".env"), override=False)

if __name__ == "__main__":
    import uvicorn
    from .lifecycle import windows_process_job, register_instance, unregister_instance

    port = int(os.getenv("KTV_PORT", "8768"))
    # Own the port before writing an instance record; a duplicate launch must not
    # overwrite the running server's shutdown identity.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", port))
        windows_process_job()
        record = register_instance(Path(__file__).resolve().parents[1], port)
        try:
            server = uvicorn.Server(
                uvicorn.Config(
                    "ktv_app.backend.app:create_app",
                    factory=True,
                    host="127.0.0.1",
                    port=port,
                )
            )
            server.run(sockets=[listener])
        finally:
            unregister_instance(record)
