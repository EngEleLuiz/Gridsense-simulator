"""Brazilian distribution feeders from ANEEL's BDGD (Phase 8.4).

BDGD (Base de Dados Geografica da Distribuidora, PRODIST Module 10) is the
geographic database every Brazilian distributor submits to ANEEL. This
package turns the low-voltage network behind one distribution transformer
into a pandapower network that the hosting-capacity studies can run on.

Nothing here is specific to one distributor, and no BDGD data is shipped
with the repository: the raw tables stay on the user's machine
(``data/`` is git-ignored) and the converted network enters a study through
``--network-file`` (see :func:`gridsense_sim.networks.register_network_file`).
"""

from .builder import BuildOptions, BuildReport, build_lv_network, list_transformers, save_network
from .reader import read_tables
from .schema import BdgdSchema

__all__ = ["BdgdSchema", "BuildOptions", "BuildReport", "build_lv_network", "list_transformers",
           "read_tables", "save_network"]
