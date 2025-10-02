import os
from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker


Base = declarative_base()
_engine = None
_SessionFactory = None


def _database_url() -> str:
    user = os.getenv("DB_USER", "oil_user")
    password = os.getenv("DB_PASSWORD", "oil_pass")
    host = os.getenv("DB_HOST", "mysql")
    port = os.getenv("DB_PORT", "3306")
    name = os.getenv("DB_NAME", "oil_wells")
    return f"mysql+mysqlconnector://{user}:{password}@{host}:{port}/{name}"


def _get_session_factory():
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_engine(_database_url(), pool_pre_ping=True)
        Base.metadata.create_all(_engine)
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _SessionFactory


def get_session():
    factory = _get_session_factory()
    return factory()


class Well(Base):
    __tablename__ = "wells"

    id = Column(Integer, primary_key=True, autoincrement=True)
    operator = Column(String(255))
    well_name = Column(String(255))
    api = Column(String(64), unique=True)
    enseco_job = Column(String(64))
    job_type = Column(String(255))
    county_state = Column(String(255))
    shl = Column(Text)
    latitude = Column(Float)
    longitude = Column(Float)
    datum = Column(String(255))

    stimulations = relationship("StimulationData", back_populates="well", cascade="all, delete-orphan")


class StimulationData(Base):
    __tablename__ = "stimulation_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    well_id = Column(Integer, ForeignKey("wells.id"), nullable=False)
    date_stimulated = Column(Date)
    stimulated_formation = Column(String(255))
    top_ft = Column(Float)
    bottom_ft = Column(Float)
    stimulation_stages = Column(Integer)
    volume = Column(Float)
    volume_units = Column(String(32))
    type_treatment = Column(String(255))
    acid = Column(String(255))
    lbs_proppant = Column(Float)
    max_treatment_pressure = Column(Float)
    max_treatment_rate = Column(Float)
    details = Column(Text)

    well = relationship("Well", back_populates="stimulations")
