import express from "express";
import { createServer as createViteServer } from "vite";
import { spawn, ChildProcess } from "node:child_process";
import net from "node:net";
import path from "node:path";
import fs from "node:fs";
import dotenv from "dotenv";
import { GoogleGenAI, ThinkingLevel } from "@google/genai";

const PROJECT_ROOT = path.resolve(process.cwd());
const PROJECT_ENV_PATH = path.join(PROJECT_ROOT, ".env");
const PROJECT_ENV_EXAMPLE_PATH = path.join(PROJECT_ROOT, ".env.example");

// Load canonical project-root .env explicitly with override:true so stale container
// parent-process variables (e.g. AI_PROVIDER=mock) never mask configured .env values.
if (fs.existsSync(PROJECT_ENV_EXAMPLE_PATH)) {
  dotenv.config({ path: PROJECT_ENV_EXAMPLE_PATH, override: false });
}
if (fs.existsSync(PROJECT_ENV_PATH)) {
  dotenv.config({ path: PROJECT_ENV_PATH, override: true });
}

// Re-apply non-empty provider credentials from .env.example if missing/empty in .env
if (fs.existsSync(PROJECT_ENV_EXAMPLE_PATH)) {
  const exampleParsed = dotenv.parse(fs.readFileSync(PROJECT_ENV_EXAMPLE_PATH));
  for (const key of ["APIFY_API_TOKEN", "GEMINI_API_KEY", "AI_PROVIDER", "AI_MODEL"]) {
    const cur = (process.env[key] || "").trim();
    const fallback = (exampleParsed[key] || "").trim();
    if ((!cur || (key === "AI_PROVIDER" && cur === "mock")) && fallback) {
      process.env[key] = fallback;
    }
  }
}

const PORT = Number(process.env.PORT || 3000);
const FASTAPI_HOST = process.env.BACKEND_HOST || "127.0.0.1";
const FASTAPI_PORT = Number(process.env.BACKEND_PORT || 8001);
const DEFAULT_LOCAL_BACKEND = `http://${FASTAPI_HOST}:${FASTAPI_PORT}`;

// Resolve effective backend origin: default to local FastAPI so local SQLite + Python worker
// power the dashboard directly; use remote Render only when USE_REMOTE_BACKEND=true is set.
const rawOrigin =
  process.env.USE_REMOTE_BACKEND === "true"
    ? process.env.BACKEND_URL || process.env.FASTAPI_ORIGIN || "https://leads-einq.onrender.com"
    : process.env.FASTAPI_ORIGIN || DEFAULT_LOCAL_BACKEND;
const FASTAPI_ORIGIN = rawOrigin.trim().replace(/\/+$/, "");

function isRemoteBackend(origin: string): boolean {
  try {
    const url = new URL(origin);
    return (
      url.protocol === "https:" ||
      (url.hostname !== "127.0.0.1" && url.hostname !== "localhost" && url.hostname !== "0.0.0.0")
    );
  } catch {
    return false;
  }
}

let fastApiProcess: ChildProcess | null = null;

function isPortListening(host: string, port: number, timeoutMs = 600): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;
    const finish = (result: boolean) => {
      if (!settled) {
        settled = true;
        socket.destroy();
        resolve(result);
      }
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
    socket.connect(port, host);
  });
}

async function ensureFastApiRunning(): Promise<void> {
  if (isRemoteBackend(FASTAPI_ORIGIN)) {
    return;
  }
  const alreadyUp = await isPortListening(FASTAPI_HOST, FASTAPI_PORT);
  if (alreadyUp) {
    return;
  }

  const venvPython = path.resolve(PROJECT_ROOT, ".venv/bin/python");
  if (!fs.existsSync(venvPython)) {
    const { execSync } = await import("node:child_process");
    execSync(
      "python3 -m venv --without-pip .venv && curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python3 && .venv/bin/pip install -q -r backend/requirements.txt",
      { cwd: PROJECT_ROOT, stdio: "inherit" }
    );
  }
  const pythonBin = fs.existsSync(venvPython) ? venvPython : "python3";

  // Build a clean environment for FastAPI + embedded worker without empty overrides
  const childEnv: NodeJS.ProcessEnv = { ...process.env };
  for (const key of [
    "APIFY_API_TOKEN",
    "APIFY_TOKEN",
    "GEMINI_API_KEY",
    "AI_PROVIDER",
    "AI_MODEL",
    "DATABASE_URL",
  ]) {
    if (key in childEnv && (!childEnv[key] || !String(childEnv[key]).trim())) {
      delete childEnv[key];
    }
  }
  childEnv.DISCOVERY_EXECUTION_MODE =
    (process.env.DISCOVERY_EXECUTION_MODE || "").trim() || "python";
  childEnv.WORKER_ENABLED = (process.env.WORKER_ENABLED || "").trim() || "true";

  fastApiProcess = spawn(
    pythonBin,
    [
      "-m",
      "uvicorn",
      "backend.app.main:app",
      "--host",
      FASTAPI_HOST,
      "--port",
      String(FASTAPI_PORT),
    ],
    {
      cwd: PROJECT_ROOT,
      stdio: "inherit",
      env: childEnv,
    }
  );

  fastApiProcess.on("exit", (code) => {
    console.log(`[LeadForge] FastAPI process exited with code ${code}`);
    fastApiProcess = null;
  });

  // Wait up to 8 seconds for FastAPI to accept connections
  for (let i = 0; i < 32; i++) {
    const ready = await isPortListening(FASTAPI_HOST, FASTAPI_PORT, 250);
    if (ready) {
      return;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
}

async function startServer() {
  if (!isRemoteBackend(FASTAPI_ORIGIN)) {
    await ensureFastApiRunning();
  }

  const app = express();
  app.use(express.json());

  // CORS & Preflight handling for frontend clients and browser previews
  app.use((req, res, next) => {
    const origin = req.headers.origin || "*";
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Access-Control-Allow-Credentials", "true");
    res.setHeader(
      "Access-Control-Allow-Methods",
      "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
    );
    res.setHeader(
      "Access-Control-Allow-Headers",
      "Content-Type, Authorization, Accept, X-Callback-Secret, X-Requested-With"
    );
    if (req.method === "OPTIONS") {
      res.status(204).end();
      return;
    }
    next();
  });

  // Security restriction for public webhook tunnels (ngrok / Cloudflare Tunnel):
  // When accessed via a public tunnel hostname, expose ONLY the authenticated n8n callback
  // endpoint and health verification endpoints, blocking the frontend UI and other API routes.
  app.use((req, res, next) => {
    const rawHost = String(req.headers["x-forwarded-host"] || req.headers.host || "")
      .split(",")[0]
      .trim()
      .toLowerCase()
      .replace(/:\d+$/, "");

    const isPublicTunnelHost =
      rawHost.endsWith(".ngrok-free.app") ||
      rawHost.endsWith(".ngrok-free.dev") ||
      rawHost.endsWith(".ngrok.io") ||
      rawHost.endsWith(".ngrok.app") ||
      rawHost.endsWith(".trycloudflare.com");

    if (isPublicTunnelHost) {
      const isAllowedCallback =
        req.method === "POST" && req.path === "/api/v1/integrations/n8n/callback";
      const isAllowedHealth =
        req.method === "GET" && (req.path === "/health" || req.path === "/api/health");

      if (!isAllowedCallback && !isAllowedHealth) {
        res.status(403).json({
          status: "forbidden",
          detail:
            "Public tunnel access is restricted exclusively to POST /api/v1/integrations/n8n/callback and GET /api/health.",
        });
        return;
      }
    }
    next();
  });

  // Server-side High-Thinking AI architectural / lead reasoning endpoint
  // Uses gemini-4-carbon with ThinkingLevel.HIGH (no maxOutputTokens)
  app.post("/api/ai/think", async (req, res) => {
    const query = typeof req.body?.query === "string" ? req.body.query.trim() : "";
    const mode = typeof req.body?.mode === "string" ? req.body.mode : "auto";

    if (!query) {
      res.status(400).json({ error: "Query is required for analysis." });
      return;
    }

    const apiKey = process.env.GEMINI_API_KEY;
    const hasValidKey = Boolean(apiKey && apiKey !== "MY_GEMINI_API_KEY" && apiKey.trim().length > 10);

    if (mode === "mock" || !hasValidKey) {
      res.json({
        provider: "mock",
        model: "gemini-4-carbon",
        thinking_level: "HIGH",
        query,
        analysis:
          "Mock AI Provider Active (Initial Development Mode): Evaluated business qualification structure. " +
          "In Phase 4 & Phase 6, AI extracts structured evidence (business_summary, personalization_observation, " +
          "potential_opportunity, icebreaker, confidence) while FastAPI computes the final deterministic lead score.",
        deterministic_note:
          "AI output provides qualitative evidence only; final lead scores are calculated deterministically by FastAPI rules.",
      });
      return;
    }

    try {
      const ai = new GoogleGenAI({
        apiKey,
        httpOptions: {
          headers: {
            "User-Agent": "aistudio-build",
          },
        },
      });

      const response = await ai.models.generateContent({
        model: "gemini-4-carbon",
        contents: query,
        config: {
          systemInstruction:
            "You are the LeadForge AI analysis engine. Provide concise, structured business discovery and qualification reasoning. Keep responses under 150 words and emphasize how evidence supports deterministic backend scoring.",
          thinkingConfig: {
            thinkingLevel: ThinkingLevel.HIGH,
          },
        },
      });

      res.json({
        provider: "gemini",
        model: "gemini-4-carbon",
        thinking_level: "HIGH",
        query,
        analysis: response.text ?? "No analysis returned.",
        deterministic_note:
          "AI output provides qualitative evidence only; final lead scores are calculated deterministically by FastAPI rules.",
      });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "Unknown Gemini API error";
      res.status(502).json({
        error: message,
        provider: "gemini",
        model: "gemini-4-carbon",
        thinking_level: "HIGH",
      });
    }
  });

  // Proxy /health and /api/* requests to the configured FastAPI backend (local or Render)
  app.use(["/health", "/api"], async (req, res) => {
    try {
      if (!isRemoteBackend(FASTAPI_ORIGIN)) {
        await ensureFastApiRunning();
      }
      const cleanBase = FASTAPI_ORIGIN.replace(/\/+$/, "");
      const cleanPath = req.originalUrl.startsWith("/") ? req.originalUrl : `/${req.originalUrl}`;
      const targetUrl = `${cleanBase}${cleanPath}`;

      const headers: Record<string, string> = {
        Accept: req.headers.accept || "application/json",
      };
      if (req.headers["content-type"]) {
        headers["Content-Type"] = String(req.headers["content-type"]);
      }
      if (req.headers["x-callback-secret"]) {
        headers["X-Callback-Secret"] = String(req.headers["x-callback-secret"]);
      }
      if (req.headers["authorization"]) {
        headers["Authorization"] = String(req.headers["authorization"]);
      }

      const init: RequestInit = {
        method: req.method,
        headers,
      };

      if (
        req.method !== "GET" &&
        req.method !== "HEAD" &&
        req.body &&
        (typeof req.body === "object" ? Object.keys(req.body).length > 0 : true)
      ) {
        init.body = typeof req.body === "string" ? req.body : JSON.stringify(req.body);
        if (!headers["Content-Type"]) {
          headers["Content-Type"] = "application/json";
        }
      }

      const upstream = await fetch(targetUrl, {
        ...init,
        signal: AbortSignal.timeout(35000),
      });
      const contentType = upstream.headers.get("content-type") || "application/json";
      const bodyText = await upstream.text();

      res.status(upstream.status).set("Content-Type", contentType).send(bodyText);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "FastAPI backend unreachable";
      res.status(503).json({
        status: "error",
        service: "leadforge-gateway",
        error: `FastAPI backend unavailable at ${FASTAPI_ORIGIN}: ${message}`,
      });
    }
  });

  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: {
        middlewareMode: true,
        hmr: false,
      },
      appType: "spa",
    });
    app.use(vite.middlewares);
  }
 else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*all", (_req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`[LeadForge] Frontend & API Gateway running on http://0.0.0.0:${PORT}`);
    console.log(`[LeadForge] Proxying /health and /api/* -> ${FASTAPI_ORIGIN}`);
  });
}

process.on("SIGINT", () => {
  if (fastApiProcess) fastApiProcess.kill();
  process.exit(0);
});

process.on("SIGTERM", () => {
  if (fastApiProcess) fastApiProcess.kill();
  process.exit(0);
});

startServer();
