import json
import urllib
import uuid
from datetime import datetime, timezone, timedelta
import mimetypes

# Add additional mimetypes
mimetypes.add_type('text/x-r', '.R', strict=True)
mimetypes.add_type('text/x-r', '.r', strict=True)
mimetypes.add_type('text/tab-separated-values', '.maf', strict=True)
mimetypes.add_type('text/tab-separated-values', '.bed5', strict=True)
mimetypes.add_type('text/tab-separated-values', '.bed', strict=True)
mimetypes.add_type('text/tab-separated-values', '.vcf', strict=True)
mimetypes.add_type('text/tab-separated-values', '.sam', strict=True)
mimetypes.add_type('text/yaml', '.yaml', strict=True)
mimetypes.add_type('text/x-markdown', '.md', strict=True)
mimetypes.add_type('text/x-markdown', '.markdown', strict=True)

import click

# Codes used to identify medical devices. Includes concepts from SNOMED CT (http://www.snomed.org/) where concept is-a 49062001 (Device) and is provided as a suggestive example.
# SCTID: 706687001 Software


@click.command()
@click.option('--document_reference', required=True, type=click.Path(exists=True), help='Path to the DocumentReference NDJSON file')
@click.option('--group', required=True, type=click.Path(exists=True), help='Path to the Group NDJSON file')
@click.option('--specimen', required=True, type=click.Path(exists=True), help='Path to the Specimen NDJSON file')
@click.option('--assay', required=True, type=click.Path(writable=True), help='Path to the output Assay NDJSON file')
def create_assay_ndjson(document_reference, group, specimen, assay):
    """Create Assay NDJSON file from DocumentReference, Group, and Specimen resources."""
    with open(document_reference, 'r') as doc_file:
        document_references = [json.loads(line.strip()) for line in doc_file]

    with open(group, 'r') as group_file:
        groups = [json.loads(line.strip()) for line in group_file]

    with open(specimen, 'r') as specimen_file:
        specimens = {spec['id']: spec for spec in (json.loads(line.strip()) for line in specimen_file)}

    # Index document references by group
    document_references_by_group = {}
    for doc in document_references:
        group_id = doc['subject']['reference'].split('/')[1]
        if group_id not in document_references_by_group:
            document_references_by_group[group_id] = []
        document_references_by_group[group_id].append(doc)

    assays = []

    # find  documents that are part of a group that has a specimen
    # R4B does not support groups of specimens
    groups_with_specimen = set()

    for group in groups:

        patient_reference = None
        specimen_references = []
        # find the specimen references in the group
        for member in group.get('member', []):
            if 'reference' in member['entity']:
                if member['entity']['reference'].startswith('Specimen/'):
                    specimen_id = member['entity']['reference'].split('/')[1]
                    specimen_references.append(member['entity']['reference'])
                    if specimen_id in specimens:
                        patient_reference = specimens[specimen_id]['subject']['reference']

        # skip if no patient or specimen references
        if not patient_reference or not specimen_references:
            continue

        groups_with_specimen.add(group['id'])

        # get all the docs for the group
        assay_documents = [
            doc
            for doc in document_references_by_group.get(group['id'], [])
        ]

        # create the assay
        assay_id = group['id']  # for now, use the group id as the assay id
        assay_dict = create_assay_refactor_docs(assay_id, patient_reference, specimen_references, assay_documents)
        assays.append(assay_dict)

    # remove groups with Specimen members
    groups = [group for group in groups if group['id'] not in groups_with_specimen]

    # find documents that have a specimen as subject
    for doc in document_references:
        if doc['subject']['reference'].startswith('Specimen/'):
            specimen_references = []
            specimen_id = doc['subject']['reference'].split('/')[1]
            specimen_references.append(doc['subject']['reference'])
            patient_reference = specimens[specimen_id]['subject']['reference']
            assert patient_reference, f"Patient reference not found for specimen {specimen_id}"
            assay_documents = [doc]
            assay_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, doc['id'] + '-assay'))
            assay_dict = create_assay_refactor_docs(assay_id, patient_reference, specimen_references, assay_documents)
            assert doc['subject']['reference'].startswith('Patient/'), f"Document subject is not a patient: {doc['subject']['reference']}"
            assays.append(assay_dict)

    docs_with_non_patient_subject = [(doc['id'], doc['subject']['reference']) for doc in document_references if not doc['subject']['reference'].startswith('Patient/')]
    assert len(docs_with_non_patient_subject) == len(groups), f"Documents have groups with non-patient subject: {docs_with_non_patient_subject}"

    with open(assay, 'w') as output_file:
        for _ in assays:
            output_file.write(json.dumps(_) + '\n')

    # TODO - pass output path, don't just re-use assay path
    with open(assay.replace('Assay', 'DocumentReference'), 'w') as output_file:
        for document_reference in document_references:
            output_file.write(json.dumps(document_reference) + '\n')
    with open(assay.replace('Assay', 'Group'), 'w') as output_file:
        for group in groups:
            output_file.write(json.dumps(group) + '\n')


def update_mime_type(doc: dict) -> dict:
    """ Update the mime type of the document to be a valid mime type."""
    attachment = doc['content'][0]['attachment']
    title = attachment.get('title', None)
    url = attachment.get('url', None)
    # get path from url
    file_name = title
    if url:
        path = urllib.parse.urlparse(url).path
        if '.' in path:
            file_name = path
    (mimetype, enc) = mimetypes.guess_type(file_name, strict=False)
    if mimetype is None:
        mimetype = 'application/octet-stream'
    assert 'vcard' not in mimetype, f"Invalid mime type for {file_name}"

    attachment['contentType'] = mimetype
    return doc


def create_assay_refactor_docs(assay_id: str, patient_reference: str, specimen_references: list[str], assay_documents: list[dict], base='R4') -> dict:
    """
    Create an R4B `Assay`, adjusting the `DocumentReference` to have the patient as the subject and the assay as a related context.

    Parameters:
    assay_id (str): The unique identifier for the assay.
    patient_reference (str): The reference to the patient associated with the assay.
    specimen_references (list[str]): A list of references to the specimens associated with the assay.
    assay_documents (list[dict]): A list of document references to be included in the assay.

    Returns:
    dict: The created assay dictionary.
    """
    assay_dict = {
        "resourceType": "ServiceRequest",
        "id": assay_id,
        "status": "completed",
        "intent": "order",
        # TODO - set category and code based on the document type
        "category": [
            {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": "108252007",
                        "display": "Laboratory procedure"
                    }
                ]
            }
        ],
        "code": {
            # 15220000 | "Laboratory test"
            # 405824009 | "Genetic test"
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "15220000",
                    "display": "Laboratory test"
                }
            ]
        },
        "subject": {"reference": patient_reference},
        #  "performer": [{"reference": "Practitioner/ETL"}],  # IT personnel merging or unmerging patient records
        "text": {
            "status": "generated",
            "div": '<div xmlns="http://www.w3.org/1999/xhtml">Autogenerated Assay. Packages references to Subject, Specimen and DocumentReference<div>'
        },
        "specimen": [{"reference": _} for _ in specimen_references],
    }

    # TODO - move this to its own function
    # now modify the document.subject to the patient and add the assay to the context.related
    for doc in assay_documents:
        doc['subject'] = {"reference": patient_reference}

        if base != 'R4':
            if 'basedOn' not in doc:
                doc['basedOn'] = []
            # set reference to the Assay in basedOn
            based_on = doc['basedOn']
            based_on.append({"reference": f"{assay_dict['resourceType']}/{assay_dict['id']}"})

            # set size to a string
            attachment = doc['content'][0]['attachment']
            if not isinstance(attachment['size'], str):
                attachment['size'] = str(attachment['size'])
        else:
            # make it a R4B document
            # these fields don't exist in R4B
            del doc['version']
            del doc['content'][0]['profile']

            # set reference to Assay in context.related
            if 'context' not in doc:
                doc['context'] = {}
            context = doc['context']
            if 'related' not in context:
                context['related'] = []
            # TODO R5 does not use related as a list of References(Any) remove it from here
            context['related'].append({"reference": f"{assay_dict['resourceType']}/{assay_dict['id']}"})

        # ensure mime type is set correctly
        update_mime_type(doc)
    return assay_dict


if __name__ == "__main__":
    create_assay_ndjson()
