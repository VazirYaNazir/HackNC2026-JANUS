# Authentication & Architecture Specification

This document details the authentication models, token lifecycles, network isolation, and available endpoints across the backend services.

---

## 1. Architecture & Port Isolation

The backend runs two isolated FastAPI application instances to enforce strict separation of privilege:

| Service | Port & Binding | Target Audience | Exposure |
| :--- | :--- | :--- | :--- |
| **Public API (`app`)** | `0.0.0.0:8000` | Mobile PWA & Agent Worker | Exposed to the internet via Cloudflare Tunnel |
| **Admin API (`admin_app`)** | `127.0.0.1:8001` | Builder UI (Desktop Host) | **Localhost only** — inaccessible from port 8000 / Tunnel |

```
                       +---------------------------------------+
                       |             Public Web /              |
                       |          Cloudflare Tunnel            |
                       +---------------------------------------+
                                           |
                                           v
                       +---------------------------------------+
                       |         Port 8000 (Public API)        |
                       |  - Mobile PWA Widgets & Execution     |
                       |  - Agent WebSocket Connection         |
                       |  * /api/v1/scripts returns 404        |
                       +---------------------------------------+
                                           |
                                  (Shared SQLite DB)
                                           |
+----------------------+                   v
| Local Host Machine   |       +---------------------------------------+
| Builder UI (Browser) | ----> |         Port 8001 (Admin API)         |
+----------------------+       |  - Script Creation & Deletion         |
(127.0.0.1 only)               |  - Pairing Code Generation (QR)       |
                               |  - Device Management & Revocation     |
                               +---------------------------------------+
```

---

## 2. Authentication Flows

### Flow A: Mobile Device Pairing (QR Code Exchange)

```
[Builder UI (Port 8001)]               [Backend DB]                  [Mobile PWA (Port 8000)]
         |                                   |                                   |
         |-- 1. GET /scripts/pairing/req --->|                                   |
         |      (with X-Admin-Secret)        |                                   |
         |                                   |                                   |
         |<- 2. {pairing_code, qr_payload} --|                                   |
         |                                   |                                   |
         |-- 3. Displays QR code on screen ------------------------------------->|
         |                                   |                                   | (Scans QR)
         |                                   |<-- 4. POST /panels/pair ----------|
         |                                   |    {pairing_code, device_name}    |
         |                                   |                                   |
         |                                   | (Validates & consumes code)       |
         |                                   | (Hashes token via SHA-256)        |
         |                                   | (Stores hash in `devices` table)  |
         |                                   |                                   |
         |                                   |--- 5. {token: "pwa_...", ...} --->|
         |                                   |                                   | (Saves in localStorage)
```

1. **Generation**: Builder UI requests a code from `127.0.0.1:8001`. A 6-character hex code (`secrets.token_hex(3).upper()`) is stored in `pairing_codes` with a 60-second expiration. Stale expired codes are cleaned up opportunistically.
2. **Consumption**: Mobile PWA sends `pairing_code` to `POST /api/v1/panels/pair` on port 8000.
3. **Single-Use Semantics**: The pairing code is deleted from `pairing_codes` within the same SQLite transaction upon retrieval, regardless of whether it succeeded or was expired. Replaying a code returns `400 Bad Request`.
4. **Token Generation & Storage**: A high-entropy random token (`pwa_<48 hex chars>`) is generated. The database stores its **SHA-256 hash**; the raw plaintext token is returned to the mobile phone once.

---

### Flow B: Authenticated Panel Operations

```
[Mobile PWA]                                                 [Public API (Port 8000)]
     |                                                                   |
     |-- GET /api/v1/panels/widgets ------------------------------------>|
     |   Header: Authorization: Bearer pwa_...                           |
     |                                                                   |
     |                                                       (Computes SHA-256 hash)
     |                                                       (Looks up hash in SQLite)
     |                                                       (Updates `last_used` timestamp)
     |                                                                   |
     |<- 200 OK with widget definitions ---------------------------------|
```

- Every request to `/api/v1/panels/widgets` or `/api/v1/panels/run/{script_id}` requires an `Authorization: Bearer <pwa_token>` header.
- The server computes `hashlib.sha256(token.encode()).hexdigest()` and verifies it against the `devices` table.
- Revoking a device from the Admin UI deletes its database entry, immediately invalidating the bearer token (returns `401 Unauthorized`).

---

### Flow C: Local Agent & Job Submissions

- **Agent Worker Connection**: The desktop agent connects to `ws://0.0.0.0:8000/ws/agent` passing `Authorization: Bearer <AGENT_TOKEN>`. The backend validates the token using constant-time comparison (`secrets.compare_digest`).
- **Job Submissions**: Calls to `POST /jobs` require an `X-Control-Key: <CONTROL_API_KEY>` header.

---

## 3. Complete Endpoint Reference

### Public API — Port 8000 (Cloudflare Tunnel & Mobile Clients)

| Method | Endpoint | Authentication | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | None | Public health check (`{"status": "ok", "service": "public"}`) |
| `POST` | `/api/v1/panels/pair` | Pairing Code (Body) | Exchanges single-use pairing code for a long-lived `pwa_...` device bearer token |
| `GET` | `/api/v1/panels/widgets` | `Bearer <pwa_token>` | Returns list of configured UI widgets for rendering on mobile |
| `POST` | `/api/v1/panels/run/{script_id}` | `Bearer <pwa_token>` | Executes a Python script in a sandboxed process with a 5-second timeout |
| `WS` | `/ws/agent` | `Bearer <AGENT_TOKEN>` | WebSocket connection endpoint for desktop automation agent worker |
| `POST` | `/jobs` | `X-Control-Key` | Dispatches an execution job to the connected agent worker |

> **Note**: All `/api/v1/scripts/*` routes are unmapped on Port 8000 and return `404 Not Found`.

---

### Admin API — Port 8001 (Localhost Only / Builder UI)

| Method | Endpoint | Authentication | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | None | Admin health check (`{"status": "ok", "service": "admin"}`) |
| `GET` | `/api/v1/scripts/` | `X-Admin-Secret` | Returns all saved Python scripts and widget configurations |
| `POST` | `/api/v1/scripts/` | `X-Admin-Secret` | Creates and persists a new script with its associated widget definition |
| `DELETE` | `/api/v1/scripts/{script_id}` | `X-Admin-Secret` | Deletes a script by ID |
| `GET` | `/api/v1/scripts/pairing/request` | `X-Admin-Secret` | Generates a 60-second pairing code and QR code payload targeting port 8000 |
| `GET` | `/api/v1/scripts/devices` | `X-Admin-Secret` | Lists all registered devices with `device_id`, `name`, `last_used`, and `is_active` |
| `DELETE` | `/api/v1/scripts/devices/{device_id}` | `X-Admin-Secret` | Revokes device access, immediately invalidating its token |

---

## 4. Environment Variables

Configure these variables in `backend/.env`:

```env
# Shared Secret for Local Builder UI on Port 8001
ADMIN_SECRET=hackathon-builder-super-secret-123

# Agent WebSocket Authentication Token on Port 8000
AGENT_TOKEN=hackathon-agent-secret-456

# Controller Key for Submitting Jobs to Port 8000
CONTROL_API_KEY=hackathon-control-secret-789

# Optional: Host/Port configuration for Admin server
ADMIN_HOST=127.0.0.1
ADMIN_PORT=8001
```
