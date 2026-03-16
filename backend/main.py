"""Entrypoint for Railpack/Nixpacks so they find a runnable script. Runs uvicorn."""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
    )
