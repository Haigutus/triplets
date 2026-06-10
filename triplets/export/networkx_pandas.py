# -------------------------------------------------------------------------------
# Name:        export/networkx_pandas.py
# Purpose:     Export triplet DataFrames to NetworkX graph format
# -------------------------------------------------------------------------------


def export_to_networkx(data):
    """Convert a triplet dataset to a NetworkX graph.

    Parameters
    ----------
    data : pandas.DataFrame
        Triplet dataset containing RDF data.

    Returns
    -------
    networkx.Graph
        A NetworkX graph with nodes (IDs with Type attributes) and edges (references).

    Notes
    -----
    - TODO: Add all node data and support additional graph export formats.

    Examples
    --------
    >>> graph = data.to_networkx()
    """
    import networkx

    #  TODO - Add all node data
    #  TODO - Add all supported graph export formats

    edges = data.references_all()
    nodes = data[["ID", "KEY", "VALUE"]].drop_duplicates().query("KEY == 'Type'")[["ID", "VALUE"]]

    graph = networkx.Graph()

    graph.add_nodes_from([(ID, {"Type": VALUE}) for ID, VALUE in nodes.values])
    graph.add_edges_from([(FROM, TO, {"Type": KEY}) for FROM, KEY, TO in edges.values])

    return graph
