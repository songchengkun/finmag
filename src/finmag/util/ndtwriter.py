import os.path
import logging
import numpy
logger = logging.getLogger(name='finmag')


class ndtWriter(object):

    datatoinclude = {
        'time': ('<s>', lambda sim: sim.time),
        'm': ('<A/m>', lambda sim: sim.m_average)
    }

    #def headers():

    def __init__(self, filename, simulation):
        self.filename = filename
        self.sim = simulation
        # if file exists, cowardly stop
        if os.path.exists(filename):
            msg = "File %s exists already; cowardly stopping" % filename
            raise RuntimeError(msg)
        self.f = open(self.filename, 'w')

    def append(self):
        for entity in sorted(self.datatoinclude.keys()):
            value = self.datatoinclude[entity][1]()
            if isinstance(value, numpy.ndarray):
                if len(value) == 3:  # 3d vector
                    for i in range(3):
                        self.f.write("%g\t" % value[i])
            self.f.write('\n')