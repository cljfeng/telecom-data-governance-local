from governance_app.config import ConfigurationError, OnlineConfig


def check_online_dependencies(config: OnlineConfig) -> None:
    """Fail before binding if the online stores cannot be reached."""
    try:
        import psycopg
    except ImportError as error:
        raise ConfigurationError("online mode requires the psycopg PostgreSQL driver") from error
    try:
        import boto3
    except ImportError as error:
        raise ConfigurationError("online mode requires the boto3 object storage client") from error

    try:
        with psycopg.connect(config.database_url, connect_timeout=5) as connection:
            connection.execute("SELECT 1")
    except Exception as error:
        raise ConfigurationError("PostgreSQL unavailable for online mode") from error

    try:
        storage = boto3.client(
            "s3",
            endpoint_url=config.object_storage_endpoint,
            region_name=config.object_storage_region,
        )
        storage.head_bucket(Bucket=config.object_storage_bucket)
    except Exception as error:
        raise ConfigurationError("object storage unavailable for online mode") from error
