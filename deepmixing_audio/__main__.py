"""Run the local two-track mixing workbench."""
import argparse
from pathlib import Path
import sys

from .server import make_server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data-dir',type=Path)
    args=parser.parse_args()
    if not 1<=args.port<=65535:
        parser.error('port须在1至65535之间')
    try:
        server=make_server(args.port,args.data_dir)
    except (OSError,RuntimeError) as exc:
        parser.exit(1,str(exc)+'\n')
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(f'deepmixing 已启动：http://127.0.0.1:{server.server_port} （仅本机）',flush=True)
    server.service.voice.prewarm_recognition()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.service.close()


if __name__=='__main__':
    main()
