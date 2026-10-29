"""Proceso local para código escrito por el usuario; no es un sandbox de seguridad."""
import contextlib
import pickle
import sys
import traceback

if __name__ == "__main__":
    try:
        frame, params = pickle.loads(sys.stdin.buffer.read())
        source = params.pop("code")
        with contextlib.redirect_stdout(sys.stderr):
            namespace = {}
            exec(compile(source,"estrategia.py","exec"),namespace)
            function = namespace["on_bar"]
            entries, exits = [], []
            for i in range(len(frame)):
                result = function(frame.iloc[:i+1].copy(),dict(params))
                if not isinstance(result,(tuple,list)) or len(result)!=2:
                    raise ValueError("on_bar debe devolver (comprar, vender).")
                import numpy as np
                if not all(isinstance(x,(bool,np.bool_)) for x in result):
                    raise ValueError("Las señales deben ser booleanos, no números ni series.")
                entries.append(bool(result[0]))
                exits.append(bool(result[1]))
        sys.stdout.buffer.write(pickle.dumps((entries,exits)))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
