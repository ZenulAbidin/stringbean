You are the orchestrator for this repository change.
Output only a JSON block with the schema below.

Task:
{{ TASK }}

Repository context:
{{ CONTEXT }}

Current repository root:
{{ REPO_ROOT }}

Create a robust implementation plan with explicit tasks.

Contract:
{
  "summary": "...",
  "assumptions": [],
  "tasks": [
    {
      "id": "task-1",
      "title": "Implement ...",
      "description": "...",
      "dependencies": [],
      "recommended_role": "implementer",
      "permissions": "read_write",
      "verification": []
    }
  ],
  "risks": [],
  "advisor_questions": []
}
