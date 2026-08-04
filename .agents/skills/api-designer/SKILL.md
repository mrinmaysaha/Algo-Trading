---
name: api-designer
description: REST API design standards, OpenAPI schemas, URL routing, HTTP response status codes, and error contracts.
argument-hint: "[api blueprint or endpoint]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# API Designer Skill

Use this skill when creating or refactoring REST API endpoints and data contracts.

## API Design Principles

1. **Consistent JSON Format**:
   - Standard response shape:
     - Success: `{"status": "success", "data": ...}`
     - Error: `{"status": "error", "message": "Reason description"}`

2. **HTTP Status Codes**:
   - `200 OK`: Successful read/update
   - `201 Created`: Resource successfully created
   - `400 Bad Request`: Missing or invalid input payload
   - `401 Unauthorized`: Missing or invalid authentication token/session
   - `404 Not Found`: Requested resource does not exist
   - `500 Internal Server Error`: Unhandled server exception

3. **Restful Naming**:
   - Use plural nouns for resources (`/api/v1/orders`, `/api/v1/positions`).
   - Use standard HTTP methods (`GET`, `POST`, `PUT`, `DELETE`).
