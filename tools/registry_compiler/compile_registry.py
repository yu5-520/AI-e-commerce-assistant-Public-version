"""Compatibility CLI shim for the external Z-Century Registry compiler."""
from tools.z_adapter.external_cli import registry_main


def main(argv=None) -> int:
    return registry_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
