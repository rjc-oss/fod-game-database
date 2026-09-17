// Upload endpoint for Fury of Dracula game-over saves.
//
// POST /upload with headers X-FoD-Database, X-FoD-Client, X-FoD-Meta and the
// .fod file as the body. Validated here, parked in KV under incoming/<id>, and
// drained into the git repo by the GitHub Action. Nothing is written to KV
// until every check has passed, so junk never spends the free-tier write quota.

const META_MAX_BYTES = 2048;
const SAVE_TYPE_RE =
  /"\$type"\s*:\s*"FuryOfDracula\.Core\.PersistentGameState, Assembly-CSharp"/;
const META_NUMBERS = [
  "influence",
  "draculaHealth",
  "daysCompleted",
  "gameType",
  "saveVersion",
  "actionCount",
];

function fail(status, message) {
  return new Response(message + "\n", {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}

// yyyyMMddTHHmmssZ — ids sort by time.
function stamp(date) {
  return date.toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname !== "/upload") return fail(404, "not found");
    if (request.method !== "POST") return fail(405, "method not allowed");

    // 2. Client and database.
    const client = request.headers.get("X-FoD-Client") || "";
    if (!client.startsWith("DraculaMod/")) return fail(400, "bad X-FoD-Client");
    const database = request.headers.get("X-FoD-Database") || "";
    if (database !== "tournament" && database !== "casual") {
      return fail(400, "bad X-FoD-Database");
    }

    // 3. Declared size, before reading anything.
    const maxBytes = parseInt(env.MAX_BODY_BYTES, 10);
    const declared = request.headers.get("Content-Length");
    if (declared === null) return fail(411, "Content-Length required");
    const declaredBytes = parseInt(declared, 10);
    if (!Number.isFinite(declaredBytes)) return fail(411, "bad Content-Length");
    if (declaredBytes > maxBytes) return fail(413, "file too large");

    // 4. Rate limit per client IP.
    if (env.UPLOAD_LIMIT) {
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      const { success } = await env.UPLOAD_LIMIT.limit({ key: ip });
      if (!success) return fail(429, "too many uploads, wait a minute");
    } else {
      console.log("UPLOAD_LIMIT binding missing; rate limiting skipped");
    }

    // 5. Meta header: small, ASCII, schema 1, numbers where numbers belong.
    const metaRaw = request.headers.get("X-FoD-Meta") || "";
    if (metaRaw.length === 0) return fail(400, "X-FoD-Meta required");
    if (metaRaw.length > META_MAX_BYTES) return fail(400, "X-FoD-Meta too large");
    if (!/^[\x20-\x7E]*$/.test(metaRaw)) return fail(400, "X-FoD-Meta not ASCII");
    let meta;
    try {
      meta = JSON.parse(metaRaw);
    } catch (e) {
      return fail(400, "X-FoD-Meta not JSON");
    }
    if (meta === null || typeof meta !== "object" || Array.isArray(meta)) {
      return fail(400, "X-FoD-Meta not an object");
    }
    if (meta.schema !== 1) return fail(400, "unsupported meta schema");
    for (const field of META_NUMBERS) {
      if (typeof meta[field] !== "number" || !Number.isFinite(meta[field])) {
        return fail(400, "X-FoD-Meta." + field + " must be a number");
      }
    }

    // 6. Body: size again (Content-Length is a claim), and it must look like a save.
    const body = new Uint8Array(await request.arrayBuffer());
    if (body.byteLength === 0) return fail(400, "empty body");
    if (body.byteLength > maxBytes) return fail(413, "file too large");
    const head = new TextDecoder("utf-8", { fatal: false }).decode(
      body.subarray(0, 256)
    );
    if (!SAVE_TYPE_RE.test(head)) return fail(400, "not a Fury of Dracula save");
    let last = body.byteLength - 1;
    while (last >= 0 && (body[last] === 0x20 || body[last] === 0x09 ||
                         body[last] === 0x0a || body[last] === 0x0d)) {
      last--;
    }
    if (last < 0 || body[last] !== 0x7d /* } */) return fail(400, "truncated save");

    // 7. Park it in KV: one envelope line, then the file verbatim.
    const receivedAtDate = new Date();
    const receivedAt = receivedAtDate.toISOString();
    const id = stamp(receivedAtDate) + "-" + crypto.randomUUID().slice(0, 8);
    const envelope = new TextEncoder().encode(
      JSON.stringify({ id, receivedAt, database, client, meta }) + "\n"
    );
    const record = new Uint8Array(envelope.byteLength + body.byteLength);
    record.set(envelope, 0);
    record.set(body, envelope.byteLength);
    try {
      await env.INCOMING.put("incoming/" + id, record, {
        expirationTtl: 7 * 24 * 3600,
      });
    } catch (e) {
      console.log("KV put failed: " + e);
      return fail(503, "database busy, try again shortly");
    }

    // 8. Poke the repo so processing is instant. The hourly cron covers failures.
    if (env.GITHUB_TOKEN) {
      ctx.waitUntil(
        fetch("https://api.github.com/repos/" + env.GITHUB_REPO + "/dispatches", {
          method: "POST",
          headers: {
            Authorization: "Bearer " + env.GITHUB_TOKEN,
            Accept: "application/vnd.github+json",
            "User-Agent": "fod-saves",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            event_type: "fod-upload",
            client_payload: { id },
          }),
        })
          .then((r) => {
            if (!r.ok) console.log("dispatch failed: " + r.status);
          })
          .catch((e) => console.log("dispatch error: " + e))
      );
    } else {
      console.log("GITHUB_TOKEN missing; relying on the cron sweep");
    }

    // 9.
    return new Response(JSON.stringify({ id }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  },
};
