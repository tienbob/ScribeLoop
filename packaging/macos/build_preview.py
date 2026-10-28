"""Assemble a separate test-only app; never modify the production artifact."""
import build_desktop as build


def main():
    build.main(app=build.BUILD / 'ScribeLoop Preview.app', fixture=True)


if __name__ == '__main__':
    main()
