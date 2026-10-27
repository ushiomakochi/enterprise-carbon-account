"""Production entry point: initialize the database, formulas and background jobs."""
import argparse


def create_app():
    from app import app, init_app
    init_app()
    return app


def main():
    import config
    parser = argparse.ArgumentParser(description="碳账户正式运行服务")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    args = parser.parse_args()
    config.HOST, config.PORT = args.host, args.port
    from waitress import serve
    serve(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
