import asyncio
import tempfile

from egress_slots import EgressSlotPool


async def check_slots():
    with tempfile.TemporaryDirectory() as lock_dir:
        pool = EgressSlotPool(("proxy-a", "proxy-b"), slots_per_proxy=2, lock_dir=lock_dir)
        leases = [pool.try_acquire(str(index)) for index in range(4)]

        assert all(leases)
        assert pool.try_acquire("full") is None
        assert {lease.proxy_url for lease in leases} == {"proxy-a", "proxy-b"}

        leases[0].release()
        replacement = await pool.acquire("replacement", timeout=1)
        assert replacement.proxy_url == leases[0].proxy_url
        replacement.release()
        for lease in leases[1:]:
            lease.release()


if __name__ == "__main__":
    asyncio.run(check_slots())
    print("egress slot check passed")
