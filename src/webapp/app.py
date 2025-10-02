import os
from typing import Dict, List

from flask import Flask, jsonify, abort

from src.db_utils import Well, StimulationData, get_session  # type: ignore


def create_app() -> Flask:
    app = Flask(__name__)

    def _stimulation_to_dict(stim: StimulationData) -> Dict[str, object]:
        return {
            "id": stim.id,
            "date_stimulated": stim.date_stimulated.isoformat() if stim.date_stimulated else None,
            "stimulated_formation": stim.stimulated_formation,
            "top_ft": stim.top_ft,
            "bottom_ft": stim.bottom_ft,
            "stimulation_stages": stim.stimulation_stages,
            "volume": stim.volume,
            "volume_units": stim.volume_units,
            "type_treatment": stim.type_treatment,
            "acid": stim.acid,
            "lbs_proppant": stim.lbs_proppant,
            "max_treatment_pressure": stim.max_treatment_pressure,
            "max_treatment_rate": stim.max_treatment_rate,
            "details": stim.details,
        }

    def _well_to_dict(well: Well, include_stimulations: bool = True) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "id": well.id,
            "api": well.api,
            "operator": well.operator,
            "well_name": well.well_name,
            "enseco_job": well.enseco_job,
            "job_type": well.job_type,
            "county_state": well.county_state,
            "shl": well.shl,
            "latitude": well.latitude,
            "longitude": well.longitude,
            "datum": well.datum,
        }
        if include_stimulations:
            payload["stimulations"] = [_stimulation_to_dict(stim) for stim in well.stimulations]
        return payload

    @app.route("/api/health", methods=["GET"])
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.route("/api/wells", methods=["GET"])
    def list_wells():
        session = get_session()
        try:
            wells: List[Well] = (
                session.query(Well)
                .order_by(Well.operator.asc(), Well.well_name.asc())
                .all()
            )
            data = [_well_to_dict(well) for well in wells]
            return jsonify(data)
        finally:
            session.close()

    @app.route("/api/wells/<string:api>", methods=["GET"])
    def get_well(api: str):
        session = get_session()
        try:
            well: Well | None = session.query(Well).filter(Well.api == api).one_or_none()
            if well is None:
                abort(404, description=f"Well with API {api} not found")
            return jsonify(_well_to_dict(well))
        finally:
            session.close()

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
