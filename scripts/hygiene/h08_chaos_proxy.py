#!/usr/bin/env python3
"""h08_chaos_proxy.py — stdlib-only asyncio TCP fault-injection proxy.

Sits between a client and the real msb-v3 server. It is a transparent TCP
relay that can inject one fault class per run:

  none               transparent relay (control / recovery path)
  latency <ms>       delay forwarding the response by <ms> milliseconds
                     (service still works, but degraded — slow success)
  drop               close the client connection without relaying any
                     response bytes (client sees connection reset / empty
                     reply — a transport-level fault, not a server failure)
  truncate <bytes>   forward only the first <bytes> bytes of the response
                     then close (client sees an incomplete reply)

Faults are applied to the *client-visible* path only; the upstream server is
never faulted. The h08 runner uses this to prove the service degrades
gracefully under injected network faults and recovers fully when the fault
path is removed.

Usage:
  python h08_chaos_proxy.py --port 18766 --upstream 127.0.0.1 --upstream-port 8766
      --fault latency --ms 500
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def _relay_client_to_upstream(reader: asyncio.StreamReader, uwriter: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            uwriter.write(data)
            await uwriter.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            uwriter.close()
        except Exception:
            pass


async def _relay_upstream_to_client(
    ureader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    fault: str,
    ms: int,
    truncate_bytes: int,
) -> None:
    sent = 0
    try:
        if fault == 'drop':
            # Simulate a dropped connection: close the client side without
            # relaying any upstream bytes.
            return
        if fault == 'latency' and ms > 0:
            await asyncio.sleep(ms / 1000.0)
        while True:
            data = await ureader.read(65536)
            if not data:
                break
            if fault == 'truncate' and sent + len(data) > truncate_bytes:
                remain = truncate_bytes - sent
                if remain > 0:
                    writer.write(data[:remain])
                    await writer.drain()
                break
            writer.write(data)
            await writer.drain()
            sent += len(data)
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, args) -> None:
    try:
        ureader, uwriter = await asyncio.open_connection(args.upstream, args.upstream_port)
    except OSError:
        # Upstream unreachable — surface the failure to the client.
        try:
            writer.close()
        except Exception:
            pass
        return

    await asyncio.gather(
        _relay_client_to_upstream(reader, uwriter),
        _relay_upstream_to_client(ureader, writer, args.fault, args.ms, args.truncate_bytes),
    )
    try:
        await writer.wait_closed()
    except Exception:
        pass


async def main(args) -> None:
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, args),
        args.bind,
        args.port,
        reuse_address=True,
    )
    print(
        f'chaos-proxy ready fault={args.fault} '
        f'bind={args.bind}:{args.port} upstream={args.upstream}:{args.upstream_port}',
        flush=True,
    )
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='h08 fault-injection proxy')
    parser.add_argument('--port', type=int, default=18766)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--upstream', default='127.0.0.1')
    parser.add_argument('--upstream-port', type=int, default=8766)
    parser.add_argument(
        '--fault', choices=['none', 'latency', 'drop', 'truncate'], default='none'
    )
    parser.add_argument('--ms', type=int, default=0)
    parser.add_argument('--truncate-bytes', type=int, default=0)
    args = parser.parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        sys.exit(0)
