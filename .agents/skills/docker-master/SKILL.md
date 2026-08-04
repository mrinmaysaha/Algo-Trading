---
name: docker-master
description: Best practices for Dockerfiles, docker-compose, container security, non-root user permissions, multi-stage builds, and port mapping.
argument-hint: "[dockerfile or compose file]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Docker Master Skill

Use this skill when inspecting, building, or troubleshooting Docker images, containers, and `docker-compose.yaml` configurations.

## Best Practices

1. **Multi-Stage Builds**:
   - Separate build dependencies (Node build, C compilers) from minimal production runtime images.

2. **Security & Permissions**:
   - Always run applications under a non-root dedicated user (e.g. `USER appuser`, UID/GID 1000).
   - Fix folder ownership (`chown -R appuser:appuser`) for all runtime data directories (`/app/db`, `/app/log`, `/app/tmp`).

3. **Networking & Ports**:
   - Expose and publish all required application ports in `docker-compose.yaml` (e.g. `5000:5000` for HTTP, `8765:8765` for WebSockets).
   - Ensure services bind to `0.0.0.0` inside containers so host port mapping works.

4. **Persistence & Volumes**:
   - Bind-mount persistent database files, keys, and logs (`./db:/app/db`) to prevent data loss across container restarts.
