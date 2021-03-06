##import io
import os
import sys
import time
import subprocess
import numpy as np
import dolfin as df
from finmag.field import Field
from finmag.util.meshes import from_geofile
from finmag.energies import Demag
from finmag.energies.demag.solver_gcr import FemBemGCRSolver
import pylab as p
import finmag.energies.demag.solver_base as sb
from finmag.physics.llg import LLG
import copy

import finmag
is_dolfin_1_1 = (finmag.util.versions.get_version_dolfin() == "1.1.0")

class FemBemGCRboxSolver(FemBemGCRSolver):
    "GCR Solver but with point evaluation of the q vector as the default"
    def __init__(self, parameters=sb.default_parameters, degree=1, element="CG",
         project_method='magpar', bench = False, qvector_method = 'box'):
        FemBemGCRSolver.__init__(self, parameters, degree, element,
         project_method, bench, qvector_method )


MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
nmagoutput = os.path.join(MODULE_DIR, 'nmag_data.dat')

# Need a clean file
if os.path.isfile(nmagoutput):
    os.remove(nmagoutput)

finmagsolvers = {"GCR": FemBemGCRSolver, "GCRbox": FemBemGCRboxSolver}

#Define data arrays to be with data for later plotting
vertices = []
initialdata = {k:[] for k in finmagsolvers.keys() + ["nmag"]}

[xavg, xmax, xmin, ymax, zmax, stddev, errorH, maxerror, errnorm] = [copy.deepcopy(initialdata) for i in range(9) ]

runtimes = {"bem": copy.deepcopy(initialdata),
           "solve": copy.deepcopy(initialdata)}

iterdict = {"poisson":[],"laplace":[]}
krylov_iter = {k:copy.deepcopy(iterdict) for k in finmagsolvers.keys()}

def printsolverparams(mesh, m, Ms):
    output = open(os.path.join(MODULE_DIR, "linsolveparams.rst"), "w")
    for name, DemagClass in finmagsolvers.items():
        demag = DemagClass()  # create a solver to read out its default linear solver data
        demag.setup(m, Ms, unit_length=1e-9)
        output.write("\nFinmag %s solver parameters:\n" % name)
        output.write("%s \n"%repr(demag.parameters.to_dict()))
        output.write("\nFinmag %s solver tolerances:" % name)
        print dir(demag)
        output.write("\nFirst linear solve :%s" %(demag.poisson_solver.parameters.to_dict()["relative_tolerance"]))
        output.write("\nSecond linear solve: %s \n \n"% (demag.laplace_solver.parameters.to_dict()["relative_tolerance"]))
    output.close()

def get_nmag_bemtime():
    """Read the nmag log to get the BEM assembly time"""

    inputfile = open("run_nmag_log.log", "r")
    nmaglog = inputfile.read()

    #The time should be between the two keywords
    keyword1 = "Populating BEM took"
    keyword2 = "seconds"

    begin = nmaglog.find(keyword1)
    end = nmaglog.find(keyword2,begin)

    time =  nmaglog[begin + len(keyword1):end]
    return float(time)


#for maxh in (2, 1, 0.8, 0.7):
meshsizes = (5, 3, 2, 1.5,1.0,0.8)
#meshsizes = (5,3,2)
for i,maxh in enumerate(meshsizes):
    # Create geofile
    geo = """
    algebraic3d

    solid main = sphere (0, 0, 0; 10)-maxh=%s ;

    tlo main;""" % str(maxh)
    absname = "sphere_maxh_%s" % str(maxh)
    geofilename = os.path.join(MODULE_DIR, absname)
    geofile = geofilename + '.geo'
    f = open(geofile, "w")
    f.write(geo)
    f.close()

    # Finmag data
    mesh = from_geofile(geofile)

    #mesh.coordinates()[:] = mesh.coordinates()[:]*1e-9 #this makes the results worse!!! HF
    print "Using mesh with %g vertices" % mesh.num_vertices()
    V = df.VectorFunctionSpace(mesh, "CG", 1, dim=3)
    DG0 = df.FunctionSpace(mesh, "DG", 0)

    # Old code
    """
    M = ("1.0", "0.0", "0.0")
    solver = FemBemGCRSolver(mesh,M)
    phi = solver.solve()
    H_demag = df.project(-df.grad(phi), V)
    """

    # Weiwei code
    """
    m = df.interpolate(df.Constant((1,0,0)), V)
    Ms = 1
    solver = SimpleFKSolver(V, m, Ms)
    H_demag = df.Function(V)
    demag = solver.compute_field()
    H_demag.vector()[:] = demag

    x, y, z = H_demag.split(True)
    x, y, z = x.vector().array(), y.vector().array(), z.vector().array()
    """

    m = Field(V, [1, 0, 0])
    Ms = Field(DG0, 1.0)

    #print solver parameters to file on the first run
    if i == 0:
        printsolverparams(mesh, m, Ms)

    #Get the number of mesh vertices for the x axis in the plots.
    vertices.append(mesh.num_vertices())

    #Get seperate data for gcr and fk solvers
    for demagtype in finmagsolvers.keys():
        #Assemble the bem and get the time.
        starttime = time.time()
        solver = finmagsolvers[demagtype]()
        solver.setup(m, Ms, unit_length=1e-9)
        demag = solver.compute_field()
        endtime = time.time()

        #Store the times
        runtimes["bem"][demagtype].append(sb.demag_timer.time("build BEM", finmagsolvers[demagtype].__name__))
        runtimes["solve"][demagtype].append(endtime - starttime)


        #store the number of krylov iterations
        krylov_iter[demagtype]["poisson"].append(solver.poisson_iter)
        krylov_iter[demagtype]["laplace"].append(solver.laplace_iter)


        H_demag = df.Function(V)
        H_demag.vector()[:] = demag
        demag.shape = (3, -1)
        x, y, z = demag[0], demag[1], demag[2]

        # Find x max and x avg
        xavg[demagtype].append(np.average(x))
        xmax[demagtype].append(max(x))
        xmin[demagtype].append(min(x))
        ymax[demagtype].append(max(abs(y)))
        zmax[demagtype].append(max(abs(z)))

        # Find standard deviation
        func = H_demag.vector().array()
        N = len(func)
        exct = np.zeros(N)
        exct[:len(x)] = -1./3*np.ones(len(x))
        sdev = np.sqrt(1./N*sum((func - exct)**2))
        stddev[demagtype].append(sdev)

        # Find errornorm
        exact = df.interpolate(df.Constant((-1./3, 0, 0)), V)
        sphere_volume=4/3.*np.pi*(10)**3
        errnorm[demagtype].append(df.errornorm(H_demag, exact, mesh=mesh)/sphere_volume)

        #actual error:
        tmperror = func-exct
        tmpmaxerror = max(abs(tmperror))
        errorH[demagtype].append(tmperror)
        maxerror[demagtype].append(tmpmaxerror)

    ####################
    #Generate Nmag Data
    ####################

    nmag_meshfile = geofilename + ".nmesh.h5"
    try:
        subprocess.call('netgen -geofile={} -meshfiletype="Neutral Format" -meshfile={}.neutral -batchmode'.format(geofile, geofilename), shell=True)
        subprocess.call('nmeshimport --netgen {}.neutral {}'.format(geofilename, nmag_meshfile), shell=True)
    except subprocess.CalledProcessError as e:
        print "Failed with returncode {}, output:\n{}".format(e.returncode, e.output)
        raise

    starttime = time.time()
    try:
        subprocess.call('nsim run_nmag.py --clean {} {}'.format(nmag_meshfile, nmagoutput), shell=True)
    except subprocess.CalledProcessError as e:
        print "Failed with returncode {}, output:\n{}".format(e.returncode, e.output)
        with open("run_nmag_log.log", "r") as f:
            print "Contents of nmag logfile:\n{}.".format(f.read())
        raise
    endtime = time.time()

    runtime = endtime - starttime
    bemtime = get_nmag_bemtime()

    runtimes["bem"]["nmag"].append(bemtime)
    runtimes["solve"]["nmag"].append(runtime - bemtime)


############################################
#Useful Plot xvalues
############################################

# Extract nmag data
f = open('nmag_data.dat', 'r')
lines = f.readlines()
f.close()
for line in lines:
    line = line.split()
    if len(line) == 3:
        xavg["nmag"].append(float(line[0]))
        xmax["nmag"].append(float(line[1]))
        stddev["nmag"].append(float(line[2]))

#p.plot(vertices, xavg["FK"], 'x--',label='Finmag FK x-avg')
#p.plot(vertices, xmax["FK"], 'o-',label='Finmag FK x-max')
#p.plot(vertices, xmin["FK"], '^:',label='Finmag FK x-min')

p.plot(vertices, xavg["nmag"], label='Nmag x-avg')
p.plot(vertices, xmax["nmag"], label='Nmag x-max')
p.title('Nmag - Finmag FK comparison')

p.xlabel('vertices')
p.grid()
p.legend(loc = 0)
p.savefig(os.path.join(MODULE_DIR, 'xvalues.png'))

############################################
#Useful Plot xvalues GCR
############################################
if not is_dolfin_1_1:
    p.figure()
    # Plot
    p.plot(vertices, xavg["GCR"], 'x--',label='Finmag GCR x-avg')
    p.plot(vertices, xmax["GCR"], 'o-',label='Finmag GCR x-max')
    p.plot(vertices, xmin["GCR"], '^:',label='Finmag GCR x-min')

    p.plot(vertices, xavg["nmag"], label='Nmag x-avg')
    p.plot(vertices, xmax["nmag"], label='Nmag x-max')
    p.title('Nmag - Finmag GCR comparison')

    p.xlabel('vertices')
    p.grid()
    p.legend(loc = 0)
    p.savefig(os.path.join(MODULE_DIR, 'xvaluesgcr.png'))

#Standard deviation plot
p.figure()
#p.plot(vertices, stddev["FK"], label='Finmag FK standard deviation')
if not is_dolfin_1_1:
    p.plot(vertices, stddev["GCR"], label='Finmag GCR standard deviation')

p.plot(vertices, stddev["nmag"], label='Nmag standard deviation')

p.xlabel('vertices')
p.title('Standard deviation')
p.grid()
p.legend(loc = 0)
p.savefig(os.path.join(MODULE_DIR, 'stddev.png'))

#Error Norm convergence plot
p.figure()
#p.plot(vertices, errnorm["FK"], label='Finmag errornorm')
p.xlabel('vertices')
p.title('Error norm')
p.grid()
p.legend(loc = 0)
p.savefig(os.path.join(MODULE_DIR, 'errnorm.png'))

#Max error plot
p.figure()
#p.plot(vertices, maxerror["FK"], 'o-',label='Finmag maxerror H_demag-x')
#p.plot(vertices, ymax["FK"], 'x-',label='Finmag maxerror H_demag-y')
#p.plot(vertices, zmax["FK"], '^-',label='Finmag maxerror H_demag-z')
p.xlabel('vertices')
p.title('Max Error per component')
p.grid()
p.legend(loc = 0)
p.savefig(os.path.join(MODULE_DIR, 'maxerror.png'))

############################################
#Useful Plot Standard deviation
############################################
p.figure()
#p.loglog(vertices, stddev["FK"], label='Finmag FK standard deviation')
if not is_dolfin_1_1:
    p.loglog(vertices, stddev["GCR"], label='Finmag GCR standard deviation')

p.loglog(vertices, stddev["nmag"], label='Nmag standard deviation')

p.xlabel('vertices')
p.title('Standard deviation (log-log)')
p.grid()
p.legend(loc = 0)
p.savefig(os.path.join(MODULE_DIR, 'stddev_loglog.png'))


############################################
#Useful Plot Error Norm log-log
############################################
p.figure()
#p.loglog(vertices, errnorm["FK"], label='Finmag FK errornorm')
if not is_dolfin_1_1:
    p.loglog(vertices, errnorm["GCR"], label='Finmag GCR errornorm')
p.loglog(vertices, errnorm["GCRbox"], label='Finmag GCR box method errornorm')

p.xlabel('vertices')
p.title('Error norm (log-log)')
p.grid()
p.legend(loc = 0)
p.savefig(os.path.join(MODULE_DIR, 'errnorm_loglog.png'))

############################################
#Useful Plot bem and solve timings
############################################
titles = ["Runtime without Bem assembly","Bem assembly times"]

for title,k in zip(titles,runtimes.keys()):
    p.figure()
    #p.loglog(vertices, runtimes[k]["FK"],'o-', label='Finmag FK timings')
    if not is_dolfin_1_1:
        p.loglog(vertices, runtimes[k]["GCR"],'x-', label='Finmag GCR timings')

    if title == "Runtime without Bem assembly":
        p.loglog(vertices, runtimes[k]["GCRbox"],'x-', label='Finmag GCR box method timings')

    p.loglog(vertices, runtimes[k]["nmag"], label='Nmag timings')

    p.xlabel('vertices')
    p.ylabel('seconds')
    p.title(title)
    p.grid()
    p.legend(loc = 0)
    p.savefig(os.path.join(MODULE_DIR, '%stimings.png'%k))

############################################
#Useful Plot krylov iterations
############################################
p.figure()
#p.plot(vertices, krylov_iter["FK"]["laplace"],'o-', label='Finmag FK laplace')
#p.plot(vertices, krylov_iter["FK"]["poisson"],'x-', label='Finmag FK poisson')
if not is_dolfin_1_1:
    p.plot(vertices, krylov_iter["GCR"]["laplace"], label='Finmag GCR laplace')
    p.plot(vertices, krylov_iter["GCR"]["poisson"], label='Finmag GCR poisson')

p.xlabel('vertices')
p.ylabel('iterations')
p.title('Krylov solver iterations')
p.grid()
p.legend(loc=0)
p.savefig(os.path.join(MODULE_DIR, 'krylovitr.png'))

print "Useful plots: errornorm_loglog.png, stddev.png, xvalues.png,xvaluesgcr.png,solvetimings.png,bemtimings,krylovitr.png"
