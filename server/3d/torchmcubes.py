"""Drop-in stand-in for `torchmcubes`, which needs a C++/CUDA toolchain to compile.
TripoSR only calls `marching_cubes(volume, threshold)`; scikit-image does the same job
from a prebuilt wheel, so nothing has to be compiled."""
import numpy as np
import torch
from skimage import measure


def marching_cubes(vol, thresh=0.0):
    v = vol.detach().float().cpu().numpy()
    try:
        verts, faces, _, _ = measure.marching_cubes(v, level=float(thresh))
    except (ValueError, RuntimeError):
        raise RuntimeError("No shape found in that image. Try a clearer photo with the object centred.")
    # Make sure the faces point outward (positive signed volume) in the final coordinates.
    tri = verts[faces]
    if np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() < 0:
        faces = faces[:, ::-1]
    verts = verts[:, [2, 1, 0]]  # TripoSR flips the axes back to (x, y, z) right after this call
    return (torch.from_numpy(np.ascontiguousarray(verts)).float(),
            torch.from_numpy(np.ascontiguousarray(faces).astype(np.int64)))
