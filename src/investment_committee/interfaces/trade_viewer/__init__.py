"""Local, read-only viewer of the simulated trades of a backtest study.

study_store, trade_view and notes use the standard library only; charts,
export and app need the optional `viewer` dependencies (Plotly, Kaleido,
Streamlit): pip install -e ".[viewer]". Nothing in simulation/ imports
this package.
"""
