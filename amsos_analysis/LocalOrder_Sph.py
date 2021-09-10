from dask.distributed import Client
import numpy as np
from numpy.lib.recfunctions import structured_to_unstructured
import scipy.spatial as ss
import meshzoo
import meshio

import Util.AMSOS as am


class Param:
    def __init__(self):
        parser = am.getDefaultArgParser('calc local stat on a spherical shell')
        parser.add_argument('-r', '--rad', type=float,
                            default=0.25,
                            help='average radius')
        parser.add_argument('-n', '--nseg', type=int,
                            default=20,
                            help='number of segments per each MT')
        parser.add_argument('-m', '--mesh', type=int,
                            default=50,
                            help='order of icosa mesh')
        parser.add_argument('-s', '--stride', type=int,
                            default=100,
                            help='snapshot stride')

        args = parser.parse_args()
        self.stride = args.stride
        config = am.parseConfig(args.config)

        R0 = config['boundaries'][0]['radius']
        R1 = config['boundaries'][1]['radius']
        center = np.array(config['boundaries'][0]['center'])
        Rc = (R0+R1)*0.5
        self.radAve = args.rad
        self.volAve = np.pi*(self.radAve**2)*np.abs(R1-R0)
        self.nseg = args.nseg  # split each MT into nseg segments
        mesh_order = args.mesh
        # a cylinder with height R1-R0, approximate
        self.foldername = 'LocalOrder'
        am.mkdir(self.foldername)

        points, self.cells = meshzoo.icosa_sphere(mesh_order)
        er, self.etheta, ep = am.e_sph(points)   # e_theta norm vectors
        # scale and shift
        points = points*Rc
        self.points = points + center[np.newaxis, :]

        print(', \n'.join("%s: %s" % item for item in vars(self).items()))

        return


def calcLocalOrder(file, param):
    '''pts: sample points, rad: average radius'''
    # step1: build cKDTree with TList center
    # step2: sample the vicinity of every pts
    # step3: compute average vol, P, S for every point
    rad = param.radAve
    volAve = param.volAve
    nseg = param.nseg
    foldername = param.foldername
    pts = param.points
    cells = param.cells
    etheta = param.etheta

    print(file)
    frame = am.FrameAscii(file, readProtein=True, sort=False, info=False)

    TList = frame.TList
    Tm = structured_to_unstructured(TList[['mx', 'my', 'mz']])
    Tp = structured_to_unstructured(TList[['px', 'py', 'pz']])
    Tvec = Tp-Tm  # vector
    Tlen = np.linalg.norm(Tvec, axis=1)  # length
    Tdct = Tvec/Tlen[:, np.newaxis]  # unit vector
    NMT = TList.shape[0]
    seg_center = np.zeros((nseg*NMT, 3))
    seg_vec = np.zeros((nseg*NMT, 3))
    seg_len = np.zeros(nseg*NMT)

    for i in range(nseg):
        seg_center[i*NMT:(i+1)*NMT, :] = Tm+((i+0.5)*1.0/nseg) * Tvec
        seg_vec[i*NMT:(i+1)*NMT, :] = Tdct
        seg_len[i*NMT:(i+1)*NMT] = Tlen/nseg

    tree = ss.cKDTree(seg_center)
    search = tree.query_ball_point(pts, rad, workers=-1, return_sorted=False)
    N = pts.shape[0]
    volfrac = np.zeros(N)
    nematic = np.zeros(N)
    polarity = np.zeros((N, 3))
    polarity_theta = np.zeros(N)
    for i in range(N):
        idx = search[i]
        if len(idx) != 0:
            vecList = seg_vec[idx]
            volfrac[i] = am.volMT(0.0125, np.sum(seg_len[idx]))/volAve
            polarity[i, :] = am.calcPolarP(vecList)
            polarity_theta[i] = np.dot(polarity[i], etheta[i])
            nematic[i] = am.calcNematicS(vecList)

    PList = frame.PList
    Pm = structured_to_unstructured(PList[['mx', 'my', 'mz']])
    Pp = structured_to_unstructured(PList[['px', 'py', 'pz']])
    Pbind = structured_to_unstructured(PList[['idbind0', 'idbind1']])
    xlinker_n_all = np.zeros(N)
    xlinker_n_db = np.zeros(N)
    centers = 0.5*(Pm+Pp)
    tree = ss.cKDTree(centers)
    search = tree.query_ball_point(pts, rad, workers=-1, return_sorted=False)
    for i in range(N):
        idx = search[i]
        if len(idx) != 0:
            xlinker_n_all[i] = len(idx)/volAve
            xList = Pbind[idx]
            xlinker_n_db[i] = np.count_nonzero(np.logical_and(
                xList[:, 0] != -1, xList[:, 1] != -1))/volAve

    name = am.get_basename(frame.filename)
    meshio.write_points_cells(foldername+"/sphere_{}.vtu".format(name), pts,
                              cells=[("triangle", cells)],
                              point_data={'volfrac': volfrac,
                                          'nematic': nematic,
                                          'polarity': polarity,
                                          'polarity_theta': polarity_theta,
                                          'xlinker_n_all': xlinker_n_all,
                                          'xlinker_n_db': xlinker_n_db
                                          })


if __name__ == '__main__':
    param = Param()
    SylinderFileList = am.getFileListSorted(
        './result*-*/SylinderAscii_*.dat', info=False)
    for file in SylinderFileList[::param.stride]:
        calcLocalOrder(file, param)
