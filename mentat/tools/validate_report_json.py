"""Validate report JSON without V8's string-size limit or shell inline quoting."""
import json
import sys


def main():
    with open(sys.argv[1], encoding="utf-8-sig") as stream:
        json.load(stream)


if __name__ == "__main__":
    main()
