# Actions and sensors

Use a scoped pairing from **Settings → Remote → Add gadget**. `GadgetRuntime`
refuses legacy Remote credentials. Each grant belongs to one workspace/Bot;
only the person can approve capability changes in native desktop Settings.

```python
from moldable_gadget import Action, Sensor, GadgetRuntime


async def set_light(args):
    await hardware.set_power(args["on"])
    actual = await hardware.read_power()
    return {"on": actual}  # return after observing the outcome


runtime = GadgetRuntime(
    client,
    actions=[
        Action(
            "set-light",
            "Set the desk light power",
            {
                "type": "object",
                "properties": {"on": {"type": "boolean"}},
                "required": ["on"],
                "additionalProperties": False,
            },
            set_light,
        )
    ],
    sensors=[
        Sensor(
            "temperature",
            "Workshop temperature",
            {"type": "number", "minimum": -40, "maximum": 125},
            "°C",
        )
    ],
)
await runtime.declare()
# Run runtime.run() in a task, and send readings from your sensor task:
await runtime.observe("temperature", 22.5)
```

The complete runnable [example](../examples/light-and-sensor.py) uses a persisted
simulated light and synthetic temperature, and requires no hardware libraries.

## Approval and schemas

A manifest has at most 16 actions and 16 sensors. IDs use 1–64 ASCII letters,
digits, dots, dashes or underscores. Descriptions are required (up to 512
characters). Action inputs are flat objects with at most 16 declared fields,
`additionalProperties: false`, and optional `required`. Each field and sensor
has a boolean, number, integer or string schema. Numeric bounds, `maxLength`
(up to 1024), and up to 32 enum choices are supported. Unknown schema keywords
are rejected. Nested objects, arrays, nullable fields and arbitrary JSON Schema
are not supported. Units are optional strings of up to 32 bytes.

Desktop fingerprints the complete descriptor. Changing its schema, description
or unit invalidates approval; queued stale actions are cancelled and claimed
actions receive a cancellation request. Reconnecting with an unchanged manifest
retains approval. Device and host both validate values before execution/storage.

## Commands and uncertain outcomes

The assigned Bot can list gadgets, submit approved actions, read command state
and request cancellation. A stable command UUID is tied to each tool invocation.
An interrupted submission returns that UUID for status reconciliation, not a
new action. A receipt of `pending` or `claimed` is not physical success.

- `pending`: persisted by the host, not yet delivered.
- `claimed`: offered to the device; an effect may be underway.
- `succeeded`: the device durably recorded the handler's observed result.
- `failed`: the device rejected a command before calling its handler.
- `cancelled`: the command was cancelled before execution.
- `expired`: its 1–120-second deadline elapsed before delivery.
- `unknown`: execution may have happened; inspect the physical state.

Handlers must be async, cancellation-aware, and finish within the deadline.
Cancellation during execution, an exception after execution begins, timeout,
or process loss before a durable receipt means **unknown**. There is no rollback
promise. A late confirmed receipt may resolve a host timeout. Results are small
JSON objects (at most 4096 encoded bytes). Prefer absolute operations such as
“set power off” over “toggle power.”

The owner-only `commands.sqlite3` journal is written before calling a handler
and before sending its result. A duplicate UUID never re-enters a handler, even
after restart. An interrupted running entry becomes unknown. Retain the journal
with the pairing for the device's lifetime. The host retains up to 256 recent
commands and never evicts unresolved entries; the device journal is the lasting
at-most-once admission record. Hardware must provide its own durable idempotency
if several independent clients can act on it. Only one process may open a paired
state directory. Close the runtime before closing its client.

`run()` reconnects transient transport failures with bounded backoff and
re-advertises the same manifest. It does not retry authorization/protocol errors.
The caller owns sensor sampling/retry and must preserve an observation UUID and
timestamp when retrying the same sample.

## Observations

`observe()` records a scalar value with its sensor ID, UUID and RFC 3339 time.
The host checks approval, schema and a timestamp within the prior seven days
(with five minutes of future clock-skew tolerance). It retains 256 readings,
deduplicates retained UUIDs and snapshots the original unit/fingerprint. The
Bot sees recent readings with timestamps, not an implicit instruction. Observing
alone never generates a Bot message, speech, browser view or action.

Native Settings shows the latest reading and recent command outcomes. A Bot on
desktop or iOS can use its same approved gadgets. A conversation arriving from
a gadget has no desktop-only screenshot/canvas or interactive browser controls;
continue tasks needing those controls on a supported screen.
