/**
 * Just enough of the protobuf wire format to read ProPresenter files without
 * a schema compiler: a message is a list of (field number → values), read
 * lazily by the caller who knows the field numbers (from the community
 * ProPresenter7-Proto schema, MIT — see `proFile.ts`).
 */

export type ProtoValue = number | Uint8Array;

export class ProtoMessage {
  private readonly fields = new Map<number, ProtoValue[]>();

  constructor(bytes: Uint8Array) {
    let i = 0;
    const varint = (): number => {
      let result = 0;
      let shift = 0;
      for (;;) {
        if (i >= bytes.length) throw new Error("truncated protobuf");
        const b = bytes[i++]!;
        // Past 2^53 precision goes, but no field we read is that large.
        result += (b & 0x7f) * 2 ** shift;
        if (b < 0x80) return result;
        shift += 7;
      }
    };
    while (i < bytes.length) {
      const key = varint();
      const field = Math.floor(key / 8);
      const wire = key & 7;
      let value: ProtoValue;
      if (wire === 0) value = varint();
      else if (wire === 2) {
        const len = varint();
        if (i + len > bytes.length) throw new Error("truncated protobuf");
        value = bytes.subarray(i, i + len);
        i += len;
      } else if (wire === 1) {
        value = new DataView(bytes.buffer, bytes.byteOffset + i, 8).getFloat64(0, true);
        i += 8;
      } else if (wire === 5) {
        value = new DataView(bytes.buffer, bytes.byteOffset + i, 4).getFloat32(0, true);
        i += 4;
      } else {
        throw new Error(`unsupported protobuf wire type ${wire}`);
      }
      const list = this.fields.get(field);
      if (list) list.push(value);
      else this.fields.set(field, [value]);
    }
  }

  private bytesOf(field: number): Uint8Array[] {
    return (this.fields.get(field) ?? []).filter((v): v is Uint8Array => v instanceof Uint8Array);
  }

  message(field: number): ProtoMessage | null {
    const b = this.bytesOf(field)[0];
    return b ? new ProtoMessage(b) : null;
  }

  messages(field: number): ProtoMessage[] {
    return this.bytesOf(field).map((b) => new ProtoMessage(b));
  }

  string(field: number): string | null {
    const b = this.bytesOf(field)[0];
    return b ? new TextDecoder().decode(b) : null;
  }

  number(field: number): number | null {
    const v = this.fields.get(field)?.[0];
    return typeof v === "number" ? v : null;
  }
}

/** ProPresenter's `rv.data.UUID { string string = 1; }`. */
export function uuidOf(m: ProtoMessage | null): string | null {
  return m?.string(1) ?? null;
}
