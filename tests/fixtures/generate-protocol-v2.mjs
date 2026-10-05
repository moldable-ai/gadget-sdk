// Independent fixture generator. Run explicitly with Node; tests never regenerate it.
import { createCipheriv, createHash, createPrivateKey, createPublicKey, hkdfSync, sign } from 'node:crypto';
import { writeFileSync } from 'node:fs';

const b64 = (value) => Buffer.from(value).toString('base64url');
const room = Buffer.from(Array.from({ length: 32 }, (_, i) => i));
const seed = Buffer.from(Array.from({ length: 32 }, (_, i) => 255 - i));
const privateKey = createPrivateKey({
  key: Buffer.concat([Buffer.from('302e020100300506032b657004220420', 'hex'), seed]),
  format: 'der', type: 'pkcs8',
});
const publicKey = createPublicKey(privateKey).export({ type: 'spki', format: 'der' }).subarray(-32);
const nonce = Buffer.from(Array.from({ length: 12 }, (_, i) => i));
const id = '00000000-0000-4000-8000-000000000001';
const messageId = '00000000-0000-4000-8000-000000000002';
const marker = 'moldable-relay-e2ee-v1';
function envelope(direction, frame, payload) {
  const fields = ['type', 'id', 'method', 'event', 'messageId'].map((key) => frame[key] ?? '');
  const key = hkdfSync('sha256', room, createHash('sha256').update(marker).digest(),
    Buffer.from(`desktop:desktop-test:${direction}`), 32);
  const cipher = createCipheriv('aes-256-gcm', key, nonce);
  cipher.setAAD(Buffer.from([marker, direction, ...fields].join('\n')));
  const ciphertext = Buffer.concat([cipher.update(JSON.stringify(payload), 'utf8'), cipher.final(), cipher.getAuthTag()]);
  frame.encrypted = { v: 1, alg: 'A256GCM', nonce: b64(nonce), ciphertext: b64(ciphertext) };
  if (direction === 'desktop-to-controller') {
    frame.signature = b64(sign(null, Buffer.from(['moldable-remote-envelope-signature-v1', ...fields,
      b64(nonce), b64(ciphertext)].join('\n')), privateKey));
  }
  return frame;
}
const params = { workspaceId: 'qa', botId: 'bot-1', body: { text: 'hello', format: 'plain' }, clientMutationId: 'mutation-1' };
const result = { result: { accepted: true } };
writeFileSync(new URL('./protocol-v2.json', import.meta.url), JSON.stringify({
  roomKey: b64(room), desktopId: 'desktop-test', desktopPublicKey: b64(publicKey),
  nonce: b64(nonce), params, result,
  request: envelope('controller-to-desktop', { type: 'req', id, method: 'bot.message.append', messageId }, params),
  response: envelope('desktop-to-controller', { type: 'res', id, messageId }, result),
}, null, 2) + '\n');
