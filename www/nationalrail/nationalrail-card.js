import {
  LitElement,
  html,
  css,
} from "https://unpkg.com/lit-element@2.0.1/lit-element.js?module";

const locale = {
  "en": {
    "entity": "Entity",
    "numRows": "Number of Rows",
    "arr_nDep": "Arrival/Depature Board",
    "arr_val": "Arrival",
    "dept_val": "Departure",
    "origin": "Origin",
    "destination": "Destination",
    "platform": "Platform",
    "departure": "Departure",
    "arrival": "Arrival",
    "operator": "Operator",
    "expected": "Expected",
    "actual": "Actual",
    "scheduled": "Scheduled",
    "estimated": "Estimated",
    "on_time": "On Time",
    "cancelled": "Cancelled",
    "arr_from": "Arrivals From",
    "dept_to": "Departures To",
    "station" : "Station",
  }
}

const fireEvent = (node, type, detail, options) => {
  options = options || {};
  detail = detail === null || detail === undefined ? {} : detail;
  const event = new Event(type, {
    bubbles: options.bubbles === undefined ? true : options.bubbles,
    cancelable: Boolean(options.cancelable),
    composed: options.composed === undefined ? true : options.composed,
  });
  event.detail = detail;
  node.dispatchEvent(event);
  return event;
};

function hasConfigOrEntityChanged(element, changedProps) {
  if (changedProps.has("_config")) {
    return true;
  }

  const oldHass = changedProps.get("hass");
  if (oldHass) {
    return (
      oldHass.states[element._config.entity] !==
      element.hass.states[element._config.entity]
    );
  }

  return true;
}

class NationalRailCard extends LitElement {

  static get properties() {
    return {
      hass: { type: Object},
      _config: { type: Object },
    };
  }

  static getConfigElement() {
    return document.createElement("national-rail-card-editor");
  }

  static getStubConfig(hass, entities, entitiesFallback) {
    const entity = Object.keys(hass.states).find((eid) =>
      Object.keys(hass.states[eid].attributes).some(
        (aid) => aid == "attribution"
      )
    );

    // console.log(entity);

    // const stations = Object.keys(entity.attributes.dests);

    return {
      entity: entity,
      numRows: 5,
      arr_nDep: false,
      // station: stations[0],
     };
  }

  // The user supplied configuration. Throw an exception and Home Assistant
  // will render an error card.
  setConfig(config) {
    if (!config.entity) {
      throw new Error("You need to define an entity");
    }

    this._config = config;
    // console.log(config)
  }

  shouldUpdate(changedProps) {
    return hasConfigOrEntityChanged(this, changedProps);
  }

  // The height of your card. Home Assistant uses this to automatically
  // distribute all cards over the available columns in masonry view
  getCardSize() {
    return 4;
  }

  // The rules for sizing your card in the grid in sections view
  // getLayoutOptions() {
  //   return {
  //     grid_rows: 3,
  //     grid_columns: 4,
  //     grid_min_rows: 3,
  //     grid_max_rows: 3,
  //   };
  // }

  _ll(str) {
    if (locale[this.lang] === undefined) {
      if (Object.keys(locale.en).includes(str)) {
        return locale.en[str];
      } else {
        return str;
      }
    } else {
      return locale[this.lang][str];
    }
  }

  static get styles() {
    return css`
      hr {
        border-color: var(--divider-color);
        border-bottom: none;
        margin: 16px 0;
      }

      .nr-label{
        font-weight: bold;
      }

      .nr-header-direction{
        font-size: medium;
        vertical-align: middle;
        font-style: italic;
        color: var(--ha-card-header-color,var(--secondary-text-color))
      }

      .nr-cancelled-colour{
        font-weight: bold;
        color: red;
      }

      .nr-other-colour{
        color: orange;
        font-weight: bold;
      }

      .nr-expected-time{
        color: aqua;
        font-weight: bold;
      }

      .nr-override-time{
        text-decoration: line-through;
      }
    `;
  }

  render() {
    if (!this._config || !this.hass) {
      return html``;
    }


    if (Object.keys(this.hass.states).includes(this._config.entity)) {
      this.entityObj = this.hass.states[this._config.entity];
      if (Object.keys(this.entityObj).includes("attributes") && Object.keys(this.entityObj.attributes).includes("dests")) {
        this.stateAttr = this.entityObj["attributes"];

        return html`
          <ha-card>
            <h1 class="card-header">
              ${this.stateAttr.station}
              <span class="nr-header-direction">
                ${(this._config.arr_nDep ? this._ll("arr_from") : this._ll("dept_to"))}
              </span>
              ${this.stateAttr.dests[this._config.station].displayName}
            </h1>
            <div class="card-content">
              ${this.renderRows()}
            </div>
          </ha-card>
        `;
      } else {
        return html`
          <ha-card>
            <h1 class="card-header"></h1>
            <div class="card-content">
              Not a national rail entity
            </div>
          </ha-card>
        `;
      }
    } else {
      return html`
        <ha-card>
          <h1 class="card-header"></h1>
          <div class="card-content">
            Select Entity
          </div>
        </ha-card>
      `;
    }

  }

  renderRows() {

    let rows = [];
    let trains = this.stateAttr.dests[this._config.station][(this._config.arr_nDep ? "Arrival" : "Departure")].trains

    // console.log(trains)

    for (let i = 0; i < this._config.numRows; i++){

      rows.push(this.createRow(this._config.arr_nDep,trains[i]));
      rows.push(html`<hr />`);
    }
    rows.pop();

    return html`${rows}`;
  }

  createRow(arr_nDep, rowInfo) {

    console.log(rowInfo)

    let plat = html`
      <tr>
        <td class="nr-label">${this._ll("platform")}: <td>
        <td class="nr-plat">${rowInfo.platform}<td>
      </tr>
    `;
    let platBefore = html``;
    let platAfter = html``;
    let departTime = html``;
    let arrivalTime = html``;
    if (arr_nDep === true) {
      platAfter = plat;
      departTime = this.getTime(rowInfo.otherEnd.st, rowInfo.otherEnd.atet);
      arrivalTime = this.getTime(rowInfo.scheduled, rowInfo.expected);
    } else {
      platBefore = plat;
      departTime = this.getTime(rowInfo.scheduled, rowInfo.expected);
      arrivalTime = this.getTime(rowInfo.otherEnd.st, rowInfo.otherEnd.et);
    }
    return html`
      <table>
        <tr>
          <td class="nr-label">${this._ll("origin")}: <td>
          <td class="nr-orig">${rowInfo.origin}<td>
        <tr>
        <tr>
          <td class="nr-label">${this._ll("destination")}: <td>
          <td class="nr-dest">${rowInfo.destination}<td>
        </tr>
        <tr>
          <td class="nr-label">${this._ll("departure")}: <td>
          <td class="nr-depart">${departTime}<td>
        </tr>
        ${platBefore}
        <tr>
          <td class="nr-label">${this._ll("arrival")}: <td>
          <td class="nr-ariv">${arrivalTime}<td>
        </tr>
        ${platAfter}
        <tr>
          <td class="nr-label">${this._ll("operator")}: <td>
          <td class="nr-oper">${rowInfo.operator}<td>
        </tr>
      </table>
    `;
  }

  addZero(i) {
    if (i < 10) {i = "0" + i}
    return i;
  }

  getTime(scheduled_in, expected_in) {

    const scheduled = new Date(Date.parse(scheduled_in));
    const expected = new Date(Date.parse(expected_in));

    console.log(scheduled)
    console.log(expected)

    if (isNaN(scheduled)) {
      if (scheduled_in == "cancelled") {
        return html`
          <span class="nr-cancelled-colour">
            ${this._ll("cancelled")}
          </span>
        `;
      } else {
        return html`
          <span class="nr-other-colour">
            ${this._ll(scheduled_in)}
          </span>
        `;
      }
    } else {
      if (expected_in != scheduled_in) {
        let elements = [];
        elements.push(html`
          <span class="nr-override-time">
            ${this.addZero(scheduled.getHours())}:${this.addZero(scheduled.getMinutes())}
          </span>&nbsp;
        `);

        if (!isNaN(expected)) {
          elements.push(html`
            <span class="nr-expected-time">
              Expected: ${this.addZero(expected.getHours())}:${this.addZero(expected.getMinutes())}
            </span>
          `);
        } else {
          if (expected_in == "Cancelled") {
            elements.push(html`
              <span class="nr-cancelled-colour">
                ${this._ll("cancelled")}
              </span>
            `);
          } else {
            elements.push(html`
              <span class="nr-other-colour">
                ${this._ll(expected_in)}
              </span>
            `);
          }
        }

        return elements;

      } else {
        return html`
          <span>
            ${this.addZero(scheduled.getHours())}:${this.addZero(scheduled.getMinutes())}
          </span>
        `;
      }
    }
  }
}


class NationalRailCardEditor extends LitElement {

  static get properties() {
    return {
      hass: { type: Object},
      _config: { type: Object },
    };
  }

  setConfig(config) {
    this._config = config;

    // console.log(config);
    // console.log(this._config);
  }

  _valueChanged(ev) {
    const config = ev.detail.value;
    fireEvent(this, "config-changed", { config });
  }

  _computeLabelCallback = (schema) => {
    if (this.hass) {
      switch (schema.name) {
        case "title":
          return this.hass.localize(
            `ui.panel.lovelace.editor.card.generic.title`
          );
        case "entity":
          return `${this.hass.localize(
            "ui.panel.lovelace.editor.card.generic.entity"
          )} (${this.hass.localize(
            "ui.panel.lovelace.editor.card.config.required"
          )})`;
        default:
          return this._ll(schema.name);
      }
    } else {
      return "";
    }
  };

  _ll(str) {
    if (locale[this.lang] === undefined) return locale.en[str];
    return locale[this.lang][str];
  }

  render() {
    // console.log(this._config);

    if (!this.hass || !this._config) {
      return html``;
    }

    this.lang = this.hass.selectedLanguage || this.hass.language;

    let stations = [];

    // console.log(this._config.entity)

    if (Object.keys(this.hass.states).includes(this._config.entity)) {
      this.entityObj = this.hass.states[this._config.entity];

      if (Object.keys(this.entityObj).includes("attributes")) {
        this.stateAttr = this.entityObj["attributes"];

        if (Object.keys(this.stateAttr).includes("dests")) {

          // console.log(this.stateAttr)

          for (const [crs, station] of Object.entries(this.stateAttr["dests"])) {
            stations.push({
              label: station["displayName"],
              value: crs
            })
          }
        }
      }

      // console.log(stations)

      // Array.foreach(this.stateAttr["dests"], function (station, crs) {
      //   stations.push({
      //     label: station["displayName"],
      //     value: crs
      //   })
      // });
    }

    const schema = [
      {
        name: "entity",
        required: true,
        selector: { entity: { domain: "sensor" } },
      },
      {
        name: "numRows",
        required: true,
        selector: { number: {mode:"box", min: 1, max: 10} },
      },
      {
        name: "arr_nDep",
        required: true,
        selector: {
          select: {
            options:[{
              label: this._ll("arr_val"),
              value: true
            }, {
              label: this._ll("dept_val"),
              value: false
            }]
          }
        }
      },
      {
        name: "station",
        required: true,
        selector: {
          select: {
            options: stations
          }
        }
      }
    ];

    const data = {
      ...this._config,
    };

    return html`<ha-form
      .hass=${this.hass}
      .data=${data}
      .schema=${schema}
      .computeLabel=${this._computeLabelCallback}
      @value-changed=${this._valueChanged}
    ></ha-form>`;
  }

}

customElements.define("national-rail-card", NationalRailCard);
customElements.define("national-rail-card-editor", NationalRailCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "national-rail-card",
  name: "National Rail Card",
  preview: true, // Optional - defaults to false
  description: "Show National Rail train schedules", // Optional
  documentationURL:
    "https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card", // Adds a help link in the frontend card editor
});