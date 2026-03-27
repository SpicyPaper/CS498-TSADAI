import multiaddr

from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.abc import IHost

from src.logging_utils import log


async def connect_to_peer(host: IHost, destination_multiaddr: str):
    """
    Connect to a remote peer using its full /p2p/... multiaddr.
    """
    log("CLIENT", f"Connecting to {destination_multiaddr}")
    maddr = multiaddr.Multiaddr(destination_multiaddr)
    info = info_from_p2p_addr(maddr)
    await host.connect(info)
    log("CLIENT", f"Connected to peer_id={info.peer_id}")
    return info


async def connect_to_bootstrap_peers(host: IHost, bootstrap_addrs: list[str]) -> None:
    """
    Best-effort bootstrap connections at startup.
    """
    for addr in bootstrap_addrs:
        try:
            await connect_to_peer(host, addr)
            log("BOOTSTRAP", f"Bootstrap connected addr={addr}")
        except Exception as exc:
            log("BOOTSTRAP", f"Failed bootstrap connect addr={addr}: {exc}")
