# Home Assistant National Rail API Integration

This is an integration for the national rail API into home assistant.
This allows you to have a schedule of the train at your station in your home assistant.

# Instalation

1. Register with national rail to get an api token [here](http://realtime.nationalrail.co.uk/OpenLDBWSRegistration/)
2. Copy the custom_components/nationalrailuk folder into your config/custom_components folder and restart home assistant
3. Find the crs code for your origin and destination station using the [National Rail website](https://www.nationalrail.co.uk/). If you live in Weybridge and commutte to Waterloo, the codes are WYB and WAT
4. You need to add 2 integration for for monitoring your morning journey WYB to WAT and one for your evening route WAT to WYB
5. This should create 2 sensors `sensor.train_schedule_wyb_wat` and `sensor.train_schedule_wat_wyb`

# Integration within the UI

## Disrubption Sensor

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

I have not cracked the UI coding yet so I did my display with an HTMl template card

    type: custom:html-template-card
    title: WYB - WAT timetable
    ignore_line_breaks: true
    content: >
    <style> table {    width: 100%;   }  tr:nth-child(even) {    
    background-color: #222222;   }  td, th {     text-align: left;   }  td.dest
    {padding-left: 1em;}

    </style>     <table>  <thead> <tr> <th>Destination</th> <th>Sch'd</th>
    <th>Expt'd</th> <th>Platform</th> </tr></thead> <tbody>
        {% for train in states.sensor.train_schedule_wyb_wat.attributes.trains -%}  <tr>
        <td> {{ train.terminus }} </td>
        <td> {{ as_timestamp(train.scheduled) | timestamp_custom('%I:%M %p') }} </td>
        <td> {% if train.expected is not string %}
        {{ as_timestamp(train.expected) | timestamp_custom('%I:%M %p') }} {% else
        %}{{ train.expected }}{%
        endif %}
        </td>
        <td> {{ train.platform }} </td>
        </tr> <tr>
        <td class="dest"> {{ train.destination }} </td>
        <td> &nbsp; </td>    
        <td> {% if train.time_at_destination is not string %}
        {{ as_timestamp(train.time_at_destination) | timestamp_custom('%I:%M %p') }} {% else
        %}{{ train.time_at_destination }}{%
        endif %}
        </td>
        <td> &nbsp; </td>
        </tr> {%- endfor %}   
            {% if (states.sensor.train_schedule_mtl_wat.attributes.trains | length) ==0 -%}
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

We are currently refreshing once every 10 minutes

# Todo

* Proper UI
* Enable via HACS
* clean up and port into core
* tests
