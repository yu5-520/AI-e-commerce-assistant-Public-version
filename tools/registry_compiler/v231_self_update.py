"""Compatibility CLI shim for the external Z-Century Self-Update compiler."""
from tools.z_adapter.external_cli import requirement_main


def main(argv=None) -> int:
    return requirement_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
