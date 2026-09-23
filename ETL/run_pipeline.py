import etl_maintenance
import etl_sites
import etl_telemetrie
import etl_equipements
import etl_orbite
import etl_catalog
# import etl_alarme

def main():
    etl_sites.main()
    etl_equipements.main()
    etl_maintenance.main()
    etl_telemetrie.main()
    etl_orbite.main()
    etl_catalog.main()
    # etl_alarm.main()

if __name__ == "__main__":
    main()