# Deploy to Render Free

Status: deployment configuration prepared; no live deployment verified.

## Configuration

The root `render.yaml` defines one Free Python web service, no database or disk.
It installs pinned requirements and binds Uvicorn to Render's `$PORT` on all
interfaces, with one worker, disabled access logs and `/health` as liveness.
The Render profile lowers global generation concurrency to 2 to limit memory
pressure. Other API limits retain their application defaults. Images remain
disabled until the selected exact model and its budgets are verified.

## Deploy

1. Push the reviewed source, including `render.yaml`, to the repository:
   `https://github.com/Kimsuncheol/screen_chat_langchain`.
2. In Render, create a Blueprint from that repository. Review that the web
   service plan is **Free** before creating resources. Alternatively create a
   Python Web Service manually with the same build/start commands and variables.
3. Enter these server-only variables in Render:

   | Variable | Value |
   | --- | --- |
   | `SC_OLLAMA_API_KEY` | A newly generated private Ollama key |
   | `SC_MODEL_ID` | Exact direct-cloud model name verified for the account |
   | `SC_FIREBASE_PROJECT_ID` | Firebase project matching the intended app build |
   | `SC_ALLOWED_UIDS` | JSON list of permitted Firebase UIDs, e.g. `["your-uid"]` |

   Use an explicit UID allowlist for initial testing. An empty list permits
   every valid user in the configured Firebase project.
4. Add a Render **Secret File** named `firebase-admin.json`, containing credentials
   for a dedicated Firebase service account with the permissions required for
   token revocation and disabled-user checks. Never commit this file or paste
   it into chat. The Blueprint points `GOOGLE_APPLICATION_CREDENTIALS` to
   `/etc/secrets/firebase-admin.json`. Without valid credentials, authenticated
   endpoints will not work even if health succeeds.
5. Deploy after configuration is complete. Record the actual HTTPS service URL
   in `docs/API.md`; do not infer it from the service name.
6. Verify `/health` and `/openapi.json`, then verify capabilities and an SSE
   request with a real permitted Firebase ID token. Confirm incremental streaming
   and disconnect cleanup through Render's proxy before integrating Flutter.
   An authenticated generation uses the Ollama account allowance.

Secrets must be entered directly into Render's private configuration. The key
previously shared in chat should be revoked and replaced, not copied into files.

## Free-plan behavior

Render spins Free web services down after 15 minutes of inactivity. Waking up
takes about a minute. Flutter should show a connecting state, allow time for
cold startup in addition to the generation deadline, and handle gateway responses
that may not use the application's JSON error schema. Do not automatically
resubmit a generation after deltas or an uncertain interrupted response.

Free services can restart at any time, have ephemeral filesystems and cannot
attach persistent disks. Process-local rate limits and daily quotas reset on
restart, deploy, or spin-down. They do not provide durable spending protection.
Do not use keep-alive jobs to imply always-on availability.

The workspace shares 750 free instance hours per month. Bandwidth/build usage
has separate allowances and possible charges or suspensions; review workspace
spend controls. Render may suspend unusually high outbound API traffic. Free
hosting does not make Ollama Cloud usage free. No paid database, Redis, disk,
or compute upgrade is configured here.

## References

- [Render FastAPI deployment](https://render.com/docs/deploy-fastapi)
- [Render Free limits](https://render.com/docs/free)
- [Render Blueprint specification](https://render.com/docs/blueprint-spec)
- [Render secrets](https://render.com/docs/configure-environment-variables)
- [Ollama Cloud](https://docs.ollama.com/cloud)
