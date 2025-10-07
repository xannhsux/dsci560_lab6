const map = L.map('map', {
    zoomControl: true,
    preferCanvas: true,
}).setView([37.5, -96.5], 4);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 18,
}).addTo(map);

const wellListElement = document.querySelector('#well-list');
const popupTemplate = document.querySelector('#popup-template');
const wellMarkers = new Map();

init();

async function init() {
    try {
        const wells = await fetchWells();
        renderWells(wells);
    } catch (error) {
        console.error('Failed to initialise map', error);
        wellListElement.innerHTML = '<li class="error">Unable to load well data. Please check the API service.</li>';
    }
}

async function fetchWells() {
    const response = await fetch('/api/wells');
    if (!response.ok) {
        throw new Error(`API responded with status ${response.status}`);
    }
    return response.json();
}

function renderWells(wells) {
    wellListElement.innerHTML = '';

    if (!Array.isArray(wells) || wells.length === 0) {
        wellListElement.innerHTML = '<li>No wells available. Run the PDF parser to populate the database.</li>';
        return;
    }

    const bounds = [];

    wells.forEach((well) => {
        const listItem = document.createElement('li');
        const title = formatWellTitle(well);
        const markerKey = markerIdentifier(well);
        listItem.textContent = title;
        listItem.tabIndex = 0;
        listItem.dataset.markerKey = markerKey;
        listItem.addEventListener('click', () => focusWell(well, markerKey));
        listItem.addEventListener('keypress', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                focusWell(well, markerKey);
                event.preventDefault();
            }
        });
        wellListElement.appendChild(listItem);

        if (isValidLatLng(well.latitude, well.longitude)) {
            const marker = L.marker([well.latitude, well.longitude], {
                title,
            });
            marker.on('click', () => showWellDetails(well));
            marker.addTo(map);
            wellMarkers.set(markerKey, marker);
            bounds.push([well.latitude, well.longitude]);
        } else {
            listItem.classList.add('no-geo');
            listItem.title = 'No valid coordinates available for this well';
        }
    });

    if (bounds.length > 0) {
        map.fitBounds(bounds, { padding: [40, 40] });
    } else {
        const hint = document.createElement('li');
        hint.textContent = 'No wells have valid latitude/longitude values yet.';
        hint.className = 'hint';
        wellListElement.appendChild(hint);
    }
}

function formatWellTitle(well) {
    const name = well.well_name || 'Unnamed Well';
    const api = well.api ? ` (${well.api})` : '';
    return `${name}${api}`;
}

function focusWell(well, markerKey) {
    if (isValidLatLng(well.latitude, well.longitude)) {
        map.setView([well.latitude, well.longitude], 10);
    }
    showWellDetails(well);
}

function showWellDetails(well) {
    const panel = document.getElementById('well-details-panel');
    const content = document.getElementById('well-details-content');

    content.innerHTML = '';
    content.appendChild(renderPopup(well));

    panel.classList.add('open');
}

function isFiniteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
}

function isValidLatLng(latitude, longitude) {
    return (
        isFiniteNumber(latitude) &&
        isFiniteNumber(longitude) &&
        latitude >= -90 &&
        latitude <= 90 &&
        longitude >= -180 &&
        longitude <= 180
    );
}

function markerIdentifier(well) {
    if (well.api && well.api.trim()) {
        return `api:${well.api}`;
    }
    return `id:${well.id}`;
}

function renderPopup(well) {
    const fragment = popupTemplate.content.cloneNode(true);
    const root = fragment.querySelector('.popup');

    const titleElement = root.querySelector('.popup-title');
    titleElement.textContent = formatWellTitle(well);

    const detailList = root.querySelector('.popup-details');
    addDetail(detailList, 'Operator', well.operator);
    addDetail(detailList, 'Job Type', well.job_type);
    addDetail(detailList, 'County / State', well.county_state);
    addDetail(detailList, 'Surface Hole Location (SHL)', well.shl);
    const coordinates = formatCoordinates(well.latitude, well.longitude);
    if (coordinates) {
        addDetail(detailList, 'Coordinates', coordinates);
    }
    addDetail(detailList, 'Datum', well.datum);

    // Web-scraped fields
    addDetail(detailList, 'Well Status', well.well_status);
    addDetail(detailList, 'Well Type', well.well_type);
    addDetail(detailList, 'Closest City', well.closest_city);

    // Production stats - populate the stat cards
    const oilValue = root.querySelector('.oil-value');
    const gasValue = root.querySelector('.gas-value');

    if (well.barrels_oil_produced && well.barrels_oil_produced > 0) {
        oilValue.textContent = formatNumber(well.barrels_oil_produced);
    } else {
        oilValue.textContent = 'N/A';
    }

    if (well.gas_produced && well.gas_produced > 0) {
        gasValue.textContent = formatNumber(well.gas_produced);
    } else {
        gasValue.textContent = 'N/A';
    }

    const stimulationsRoot = root.querySelector('.stimulations');
    if (Array.isArray(well.stimulations) && well.stimulations.length > 0) {
        well.stimulations.forEach((stim) => {
            const article = document.createElement('article');
            const list = document.createElement('dl');

            addDetail(list, 'Stimulated Formation', stim.stimulated_formation);
            addDetail(list, 'Date Stimulated', formatDate(stim.date_stimulated));
            addDetail(list, 'Stages', formatNumber(stim.stimulation_stages));
            addDetail(list, 'Top (ft)', formatNumber(stim.top_ft));
            addDetail(list, 'Bottom (ft)', formatNumber(stim.bottom_ft));
            addDetail(list, 'Volume', formatVolume(stim.volume, stim.volume_units));
            addDetail(list, 'Treatment Type', stim.type_treatment);
            addDetail(list, 'Acid', stim.acid);
            addDetail(list, 'Proppant (lbs)', formatNumber(stim.lbs_proppant));
            addDetail(list, 'Max Pressure', formatNumber(stim.max_treatment_pressure));
            addDetail(list, 'Max Rate', formatNumber(stim.max_treatment_rate));
            // Skip Details field - often contains OCR artifacts and form text
            // if (stim.details) {
            //     addDetail(list, 'Details', stim.details);
            // }

            article.appendChild(list);
            stimulationsRoot.appendChild(article);
        });
    } else {
        const message = document.createElement('p');
        message.textContent = 'No stimulation data captured for this well.';
        stimulationsRoot.appendChild(message);
    }

    const pdfPlaceholder = root.querySelector('.pdf-link');
    pdfPlaceholder.textContent = 'PDF report: access original files via the /pdfs volume inside the project.';

    const crawlerPlaceholder = root.querySelector('.crawler-info');
    if (well.well_status && well.well_status !== 'N/A') {
        crawlerPlaceholder.textContent = `Web data enriched from drillingedge.com: Status=${well.well_status}, Type=${well.well_type}, City=${well.closest_city}`;
    } else {
        crawlerPlaceholder.textContent = 'Crawler data: Run web_scraper service to enrich wells with data from drillingedge.com.';
    }

    const wrapper = document.createElement('div');
    wrapper.appendChild(root);
    return wrapper.firstElementChild;
}

function addDetail(container, label, value) {
    if (value === null || value === undefined || value === '') {
        return;
    }
    // Skip fields with "N/A" default values
    if (value === 'N/A' || value === 0 || value === '0') {
        return;
    }
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = value;
    container.appendChild(dt);
    container.appendChild(dd);
}

function formatDate(value) {
    if (!value) {
        return null;
    }
    try {
        return new Date(value).toLocaleDateString();
    } catch (error) {
        return value;
    }
}

function formatNumber(value) {
    if (!isFiniteNumber(value)) {
        return null;
    }
    return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatVolume(volume, units) {
    if (!isFiniteNumber(volume)) {
        return null;
    }
    return `${Number(volume).toLocaleString(undefined, { maximumFractionDigits: 2 })}${units ? ` ${units}` : ''}`;
}

function formatCoordinates(latitude, longitude) {
    if (!isValidLatLng(latitude, longitude)) {
        return null;
    }
    const latHemisphere = latitude >= 0 ? 'N' : 'S';
    const lonHemisphere = longitude >= 0 ? 'E' : 'W';
    const latValue = Math.abs(Number(latitude)).toFixed(5);
    const lonValue = Math.abs(Number(longitude)).toFixed(5);
    return `${latValue}° ${latHemisphere}, ${lonValue}° ${lonHemisphere}`;
}
