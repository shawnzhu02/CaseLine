from caseline.main import create_app

# Vercel Python entrypoint (FastAPI). Settings come from project environment variables.
app = create_app()
