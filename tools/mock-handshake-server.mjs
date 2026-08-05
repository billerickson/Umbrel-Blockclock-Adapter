#!/usr/bin/env node

import net from "node:net";
import crypto from "node:crypto";

const listenHost = process.env.LISTEN_HOST || "192.168.10.30";
const listenPort = Number(process.env.LISTEN_PORT || 21021);

function encodeUnsigned(value) {
  if (value < 24) return Buffer.from([value]);
  if (value < 256) return Buffer.from([0x18, value]);
  if (value < 65536) {
    const result = Buffer.alloc(3);
    result[0] = 0x19;
    result.writeUInt16BE(value, 1);
    return result;
  }
  throw new Error("integer is too large for fixture encoder");
}

function encodeBytes(value) {
  const length = encodeUnsigned(value.length);
  length[0] |= 0x40;
  return Buffer.concat([length, value]);
}

function encodeText(value) {
  const bytes = Buffer.from(value, "utf8");
  const length = encodeUnsigned(bytes.length);
  length[0] |= 0x60;
  return Buffer.concat([length, bytes]);
}

function encodeArray(values) {
  if (values.length >= 24) throw new Error("fixture array is too large");
  return Buffer.concat([Buffer.from([0x80 | values.length]), ...values.map(encode)]);
}

function encodeMap(value) {
  const entries = Object.entries(value);
  if (entries.length >= 24) throw new Error("fixture map is too large");
  const parts = [Buffer.from([0xa0 | entries.length])];
  for (const [key, item] of entries) parts.push(encodeText(key), encode(item));
  return Buffer.concat(parts);
}

function encode(value) {
  if (Buffer.isBuffer(value)) return encodeBytes(value);
  if (typeof value === "string") return encodeText(value);
  if (Number.isInteger(value) && value >= 0) return encodeUnsigned(value);
  if (Array.isArray(value)) return encodeArray(value);
  if (value && typeof value === "object") return encodeMap(value);
  throw new Error(`unsupported fixture value: ${value}`);
}

function frame(payload) {
  const header = Buffer.alloc(4);
  header[0] = 0xbc;
  header.writeUIntBE(payload.length, 1, 3);
  return Buffer.concat([header, payload]);
}

const server = net.createServer((socket) => {
  console.log(`${new Date().toISOString()} connected ${socket.remoteAddress}:${socket.remotePort}`);

  const challenge = crypto.randomBytes(32);
  socket.write(frame(encode({ min_version: [0, 1, 1], challenge })));

  socket.on("data", (data) => {
    console.log(`${new Date().toISOString()} client ${data.toString("hex")}`);
  });
  socket.on("error", (error) => console.error(error.message));
  socket.on("close", () => console.log(`${new Date().toISOString()} disconnected`));
});

server.listen(listenPort, listenHost, () => {
  console.log(`local handshake fixture listening on ${listenHost}:${listenPort}`);
});
