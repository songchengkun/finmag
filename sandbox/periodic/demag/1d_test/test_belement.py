import numpy as np
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt

import dolfin as df


mesh = df.BoxMesh(-5, -5, -5, 5, 5, 5, 5, 5, 5)

from finmag.native.treecode_bem import compute_solid_angle_single
from finmag.native.treecode_bem import compute_boundary_element
from finmag.native.llg import compute_lindholm_L

def test2(z=0.1):
    
    vert_bsa =np.zeros(4)
    
    mc = np.array([0,1,2,3])
    p0 = np.array([0.,0.,0.])
    p1 = np.array([1.9999,0.,0.])
    p2 = np.array([0.,1.2,0.])
    p = np.array([0.,2.,z])
    xyz = np.array([p,p0,p1,p2])
    
    for j in range(4):
        tmp_omega = compute_solid_angle_single(
                    xyz[mc[j]],
                    xyz[mc[(j+1)%4]],
                    xyz[mc[(j+2)%4]],
                    xyz[mc[(j+3)%4]])

        vert_bsa[mc[j]]+=tmp_omega
    
    print vert_bsa/(4*np.pi)

def test_boundary_element(z=0.1):
    p0 = np.array([0.,0.,0.])
    p1 = np.array([1.,0.,0.])
    p2 = np.array([0.,1.,0.])
    p = np.array([0.,0.,z])
    
    be = np.array([0.,0.,0.])
    
    compute_boundary_element(p,p0,p1,p2,be)
    
    be2 = compute_lindholm_L(p,p0,p1,p2)
    
    print "[DDD Weiwei]: ", (z, be)
    print "[DDD Dmitri]: ", (z, be2)

    
    return be2
    
def plot_mx():
    
    zs=np.linspace(0,1e-2,101)
    bs = []
    for z in zs:
        r = test_boundary_element(z)
        bs.append(r[0])
    
    fig=plt.figure()
    plt.plot(zs, bs, '.-',label='field')
    #plt.plot(ns, field_pbc, '.-',label='field2')
    #plt.plot(ts, data['E_Demag'], label='Demag')
    #plt.plot(ts, data['E_Exchange'], label='Exchange')
    #plt.xlabel('copies')
    #plt.ylabel('field')
    
    plt.legend()
    
    fig.savefig('res.pdf')


if __name__ == '__main__':
    #print test_boundary_element(z=1e-10)
    #plot_mx()
    test2(z=0.01)