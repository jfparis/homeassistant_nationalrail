# Home Assistant National Rail API Integration

This is an integration for the national rail API into home assistant.
This allows you to have a schedule of the train at your station in your home assistant.

# Installation

1. Register with national rail to get an api token [here](http://realtime.nationalrail.co.uk/OpenLDBWSRegistration/)
2. Copy the custom_components/nationalrailuk folder into your config/custom_components folder and restart home assistant
3. Find the crs code for your origin and destination station using the [National Rail website](https://www.nationalrail.co.uk/). If you live in Weybridge and commutte to Waterloo, the codes are WYB and WAT
4. You need to add 2 integration for for monitoring your morning journey WYB to WAT and one for your evening route WAT to WYB
5. This should create 2 sensors `sensor.train_schedule_wyb_wat` and `sensor.train_schedule_wat_wyb`

# Integration within the UI

## Disruption Sensor

You might want to create a binary sensor to be alerted of disruptions

    template: 
    - binary_sensor:
        - unique_id: train_perturbation_mtl_wat
        state: "{{state_attr('sensor.train_schedule_wyb_wat', 'perturbations') }}"
        attributes:
            friendly_name: "Perturbation on the Weybridge -> Waterloo line"

        - unique_id: train_perturbation_wat_mtl
        state: "{{state_attr('sensor.train_schedule_wat_wyb', 'perturbations') }}"
        attributes:
            friendly_name: "Perturbation on the Waterloo -> Weybridge line"

## Departing board display

I have not cracked the UI coding yet so I did my display with an [HTML template card](https://github.com/PiotrMachowski/Home-Assistant-Lovelace-HTML-Jinja2-Template-card).

    type: custom:html-template-card
    title: WYB - WAT timetable
    ignore_line_breaks: true
    content: >
        <style> table {width: 100%;}  tr:nth-child(even) { background-color: #222222;}  td, th {text-align: left;} td.dest {padding-left: 1em;}
        </style> 
        <table>  
        <thead> <tr> <th>Destination</th> <th>Sch'd</th> <th>Expt'd</th>
        <th>Platform</th> </tr></thead> 
        {% if states.sensor.train_schedule_wyb_wat and (states.sensor.train_schedule_wyb_wat.attributes.trains | length) > 0 -%}
        <tbody>
        {% for train in states.sensor.train_schedule_wyb_wat.attributes.trains %}<tr>
            <td>{{ train.terminus }}</td>
            <td>{{ as_timestamp(train.scheduled) | timestamp_custom('%I:%M %p') }}</td>
            <td>{% if train.expected is not string -%}
            {{ as_timestamp(train.expected) | timestamp_custom('%I:%M %p') }}
            {%- else -%}{{ train.expected -}}
            {% endif %}</td>
            <td>{{ train.platform }}</td>
        </tr>
        {% for dest in train.destinations -%}  <tr>
            <td class="dest">{{ dest.name }}</td>
            <td>&nbsp;</td>    
            <td>{% if dest.time_at_destination is not string -%}
            {{ as_timestamp(dest.time_at_destination) | timestamp_custom('%I:%M %p') }} 
            {%- else -%}{{ dest.time_at_destination -}}
            {% endif %}</td><td></td>
        </tr> 
        {% endfor -%}
        {%- endfor -%}   
        {%- else -%}
        <tr><td>No Trains</td></tr>
        {%- endif -%}
        </tbody> </table> 


## Automation and notifications

I created 4 zone. 
* Neighbourhood (includes my local station)
* Weybridge station
* Waterloo station
* City (where I work)

I want
* platform notification when I reach the station
* notification of delays in the morning on the WYB->WAT line if I am still around home 
* notification of delays in the evwning on the WAT->WYB line if I am still around work 

A blueprint is included in the repository. Follow the docs 
[here](https://www.home-assistant.io/docs/automation/using_blueprints/)


# Fair use policy

National Rail limits API call to five million requests per four week railway period.
An update every minute for a 4 week period would require 40,320 request. You could therefore have 124 those sensors.

We are currently refreshing once every 15 minutes in normal conditions but starts updating every minutes if we are within 5 minutes of the planned departure time or if the train is delayed

# Support / Questions

Please raise questions in the thread on the 
[home assistant forum](https://community.home-assistant.io/t/national-rail-integration/529940/18)

# Todo

* Proper UI
* clean up and port into core
* tests
