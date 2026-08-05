#!/usr/bin/env node

import crypto from "node:crypto";
import net from "node:net";

const blockclockHost = process.env.BLOCKCLOCK_HOST || "192.168.40.20";
const localAddress = process.env.LOCAL_ADDRESS || "192.168.10.30";
const backend = process.argv[2];

if (!backend) {
  console.error("usage: node tools/set-blockclock-backend.mjs <host-or-host:port>");
  process.exit(2);
}

function websocketFrame(text) {
  const payload = Buffer.from(text, "utf8");
  if (payload.length >= 126) throw new Error("fixture payload is too large");

  const mask = crypto.randomBytes(4);
  const frame = Buffer.alloc(2 + 4 + payload.length);
  frame[0] = 0x81;
  frame[1] = 0x80 | payload.length;
  mask.copy(frame, 2);
  for (let index = 0; index < payload.length; index += 1) {
    frame[6 + index] = payload[index] ^ mask[index % 4];
  }
  return frame;
}

const socket = net.createConnection({
  host: blockclockHost,
  port: 80,
  localAddress,
});

const key = crypto.randomBytes(16).toString("base64");
let upgraded = false;
let buffered = Buffer.alloc(0);

socket.setTimeout(15000);
socket.on("connect", () => {
  socket.write([
    "GET /websocket HTTP/1.1",
    `Host: ${blockclockHost}`,
    "Upgrade: websocket",
    "Connection: Upgrade",
    `Sec-WebSocket-Key: ${key}`,
    "Sec-WebSocket-Version: 13",
    "",
    "",
  ].join("\r\n"));
});

socket.on("data", (chunk) => {
  buffered = Buffer.concat([buffered, chunk]);
  if (upgraded) return;

  const headerEnd = buffered.indexOf("\r\n\r\n");
  if (headerEnd === -1) return;

  const headers = buffered.subarray(0, headerEnd).toString("utf8");
  if (!headers.startsWith("HTTP/1.1 101") && !headers.startsWith("HTTP/1.0 101")) {
    throw new Error(`websocket upgrade failed: ${headers.split("\r\n")[0]}`);
  }

  upgraded = true;
  socket.write(websocketFrame(`${JSON.stringify({ action: "_connected", arg: "/prefs" })}\n`));
  socket.write(websocketFrame(`${JSON.stringify({
    action: "api",
    arg: { action: "set", noun: "backend", arg: backend },
  })}\n`));

  setTimeout(() => {
    console.log(`Blockclock backend set to ${backend}`);
    socket.end();
  }, 1000);
});

socket.on("timeout", () => socket.destroy(new Error("connection timed out")));
socket.on("error", (error) => {
  console.error(error.message);
  process.exitCode = 1;
});
