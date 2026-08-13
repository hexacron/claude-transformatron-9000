# Ensure transforms are discovered: add an import per module under transforms/.
# The server only registers what this file imports.

from maltego.server import MaltegoServerSettings, ServerHTTPSettings, run_server

if __name__ == "__main__":
    settings = MaltegoServerSettings(
        server_name="New Maltego Integration",
        ns="acme.new_maltego_integration",  # choose acme.* here
        author="Acme Corp",
        http_settings=ServerHTTPSettings(
            protocol="http",
            cors_allowed_origins=["https://app.maltego.com"]
        ),
    )

    run_server(settings=settings)
