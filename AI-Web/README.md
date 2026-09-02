# QA AI Studio Web

The web migration is a FastAPI service with the supplied redesign frontend at
`Frontend/index.html`. It serves the frontend and API from one local process.

## Run locally

From this `AI-Web` directory, install the web dependencies and the existing
AI/runtime dependencies, then start Uvicorn:

```powershell
python -m pip install -r requirements-web.txt
python -m pip install -r ..\AI\requirements.txt
python -m uvicorn Web.main:app --reload --port 8000
```

Open `http://localhost:8000`. The API health check is at
`http://localhost:8000/api/health`.

## What is implemented

- Authentication, roles, permissions, and audit logs
- Knowledge Hub
- QA Automation backend
- AI Assistant backend
- Dashboard snapshot (`GET /api/dashboard/snapshot`) using live SQLite data

QA Engineering and Settings remain the next unimplemented web milestones.
