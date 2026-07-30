# Wake journal fixtures

`benign-global.json-seq.b64` is a systemd 260 `journalctl` capture from the
deployed Pi. It contains an unrelated kernel record, one benign
`cec-tv-wake.service` record, three unrelated system-manager records, and the
terminal `--show-cursor` line.

The capture used:

```text
journalctl -b --after-cursor=… --lines=+5 \
  --output=json-seq --show-cursor \
  --output-fields=MESSAGE,PRIORITY,_PID,_SYSTEMD_INVOCATION_ID,\
_SYSTEMD_UNIT,_UID,INVOCATION_ID,UNIT,OBJECT_SYSTEMD_UNIT,COREDUMP_UNIT
```

Boot, invocation, sequence, cursor, and PID identifiers were scrubbed through
literal byte replacement. The JSON was not decoded or re-encoded, so record
separators, line feeds, field ordering, and producer formatting remain as
emitted. Base64 keeps the leading JSON-sequence record-separator bytes safe in
Git and patch tooling.

`benign-records.json` is a readable synthetic fixture used to construct
targeted malformed and boundary cases. It is not producer-conformance
evidence.
