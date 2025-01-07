import importlib

import click
import json
from pydantic import ValidationError

# Import the R4 classes
FHIR_CLASSES = importlib.import_module('fhir.resources.R4B')


def transform_documentreference(resource):
    # Transformation logic for DocumentReference (R5 to R4)
    del resource["version"]
    if "content" in resource:
        for content in resource["content"]:
            if "profile" in content:
                content["format"] = content.pop("profile")[0]["valueCoding"]
    # if "custodian" in resource:
    #     resource["custodianOrganization"] = resource.pop("custodian")

    #
    # TODO - remove when data is cleaned up
    #

    if "subject" in resource and "reference" in resource["subject"]:
        if "Specimen" in resource["subject"]["reference"]:
            resource["context"] = {"related": [{"reference": resource["subject"]["reference"]}]}
            resource.pop("subject")
            # return None
    return resource


def transform_bodystructure(resource):
    # Transformation logic for BodyStructure (R5 to R4)
    if "includedStructure" in resource:
        resource["location"] = resource.pop("includedStructure")[0]["structure"]
    return resource


def transform_encounter(resource):
    # Transformation logic for Encounter (R5 to R4)
    if "reason" in resource:
        resource["reasonReference"] = [ref["reference"] for ref in resource.pop("reference", [])]
    if "class" in resource:
        resource["class"] = resource["class"]["coding"][0]
    else:
        resource["class"] = {"code": "NONAC", "display": "inpatient non-acute"}
    resource["status"] = "finished"
    return resource


def transform_group(resource):
    # Transformation logic for Group (R5 to R4)
    del resource["membership"]
    resource["actual"] = True
    # Group.type: code type mismatch: "specimen" is not a GroupTypeCode"
    resource["type"] = "person"
    return resource


def transform_imagingstudy(resource):
    # Transformation logic for ImagingStudy (R5 to R4)
    if "basedOn" in resource:
        resource["procedureReference"] = resource.pop("basedOn")
    if "series" in resource:
        for series in resource["series"]:
            if "modality" in series:
                series["modality"] = series["modality"]["coding"][0]
                series["modality"]["system"] = series["modality"]["system"].replace(" ", "")
    return resource


def transform_medicationadministration(resource):
    # Transformation logic for MedicationAdministration (R5 to R4)
    if "medication" in resource:
        _medication = resource.pop("medication")
        if "concept" in _medication:
            resource["medicationCodeableConcept"] = _medication.pop("concept")
        else:
            resource["medicationReference"] = _medication.pop("reference")
        resource["effectiveDateTime"] = resource.pop("occurenceDateTime")
        if "category" in resource:
            resource["category"] = resource["category"][0]
    if "medicationCodeableConcept" in resource:
        resource["medicationCodeableConcept"]["coding"][0]["system"] = resource["medicationCodeableConcept"]["coding"][0]["system"].replace("'", "")
    return resource


def transform_researchstudy(resource):
    # Transformation logic for ResearchStudy (R5 to R4)
    if "name" in resource:
        resource.pop("name")
    return resource


def transform_researchsubject(resource):
    # Transformation logic for ResearchSubject (R5 to R4)
    resource["individual"] = resource.pop("subject")
    resource["status"] = "on-study"
    return resource


def transform_specimen(resource):
    # Transformation logic for Specimen (R5 to R4)
    if "processing" in resource:
        for process in resource["processing"]:
            process["procedure"] = process.pop("method")
    if "collection" in resource:
        if "procedure" in resource["collection"]:
            del resource["collection"]["procedure"]
    return resource


def dispatch_transformation(resource: dict) -> dict|None:
    transformers = {
        "DocumentReference": transform_documentreference,
        "BodyStructure": transform_bodystructure,
        "Encounter": transform_encounter,
        "Group": transform_group,
        "ImagingStudy": transform_imagingstudy,
        "MedicationAdministration": transform_medicationadministration,
        "ResearchStudy": transform_researchstudy,
        "ResearchSubject": transform_researchsubject,
        "Specimen": transform_specimen,
    }

    resource_type = resource.get("resourceType")
    if resource_type in transformers:
        return transformers[resource_type](resource)
    else:
        raise ValueError(f"Unsupported resourceType: {resource_type}")


def validate_r4_resource(resource):
    try:
        klass = FHIR_CLASSES.get_fhir_model_class(resource['resourceType'])
        _ = klass.model_validate(resource)
        return True  # If no exceptions, it's valid
    except ValidationError as e:
        for error in e.errors():
            # Ignore the error about attachment.size, R4 has it as an unsignedInt, R5 has it as an integer64
            if '.'.join([str(_) for _ in error['loc']]) == 'content.0.attachment.size':
                return True
        # raise e
        click.echo(f"Validation error: {klass} {e}\n{json.dumps(resource, indent=2)}")
        return False


@click.command()
@click.option('--input-ndjson', required=True, type=click.Path(exists=True), help='Path to the input NDJSON file')
@click.option('--output-ndjson', required=True, type=click.Path(writable=True), help='Path to the output NDJSON file')
@click.option('--validate', is_flag=True, default=True, help='Validate transformed resources for R4 compliance')
@click.option('--stop-on-first-error', is_flag=True, default=False, help='Stop processing on the first error')
def process_ndjson(input_ndjson, output_ndjson, validate, stop_on_first_error):
    """Process an NDJSON file to transform R5 resources to R4 equivalents."""
    with open(input_ndjson, 'r') as infile, open(output_ndjson, 'w') as outfile:
        for line in infile:
            resource = json.loads(line.strip())
            try:
                transformed_resource = dispatch_transformation(resource)
                if not transformed_resource:
                    continue
                if validate:
                    if not validate_r4_resource(transformed_resource):
                        if stop_on_first_error:
                            exit(1)
                outfile.write(json.dumps(transformed_resource) + '\n')
            except ValueError as e:
                click.echo(f"Error processing resource: {e}")
                if stop_on_first_error:
                    exit(1)


if __name__ == "__main__":
    process_ndjson()

