# SDL3 headers

The public headers of [SDL](https://github.com/libsdl-org/SDL) 3.2.30 (`include/SDL3/`, from `SDL3-3.2.30.tar.gz`, SHA-256 `4c3b09330d866dc52eb65b66259a6684ad387252ca8c57901b3a2b534eb42e3d`), zlib license (`LICENSE.txt`). The test, EGL and OpenGL headers are left out; nothing here includes them.

The SteamOS host does not link SDL: it opens the system's `libSDL3.so.0` at run time (`dm/steamos/sdl_dyn.c`) and checks every function it takes. These headers are the 3.2 API, so the host uses nothing a 3.2 or later runtime lacks; SDL3 keeps its ABI within 3.x.
