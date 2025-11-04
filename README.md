# Project COMPASS

**COMPASS**: **C**omponent and **O**perational **M**aintenance **P**lanning **A**nalytics **S**ervice **S**ystem

---

## 1. Project Overview

Project COMPASS is a proof-of-concept (PoC) initiative designed to revolutionize how the Federal Aviation Administration (FAA) approaches equipment sustainment and logistics. The project aims to build a modern analytics platform on Amazon Web Services (AWS) that leverages Generative AI to unlock critical intelligence currently siloed in disparate data sources.

By integrating unstructured data from PDF technical manuals with structured data from financial and supply chain systems, Project COMPASS will provide a unified, queryable interface where engineers and planners can ask complex questions in plain English and receive synthesized, data-driven answers.

## 2. The Problem

Currently, FAA sustainment analysis is hampered by significant data fragmentation:

* **Unstructured Data:** Vital information on part configurations, maintenance procedures, and system schematics is locked within hundreds of static, non-searchable PDF technical guides.

* **Siloed Systems:** Operational data is spread across multiple, disconnected systems, including Delphi for accounting, IFS ERP for supply chain management, and others like UTBS, WMS, and PRISM.

* **Inefficient Analysis:** Answering a simple question like "What is the total cost and current inventory for all components in an ASR-9 radar's generator?" requires manually cross-referencing multiple documents and systems, a process that is slow, error-prone, and inefficient.

## 3. The Solution

Project COMPASS addresses this challenge by creating an intelligent data-fabric layer that automates the extraction, integration, and analysis of this information. The core of the solution is an AI-driven system that:

1. **Extracts Intelligence:** Uses Amazon Textract to "read" and understand the content of PDF manuals, extracting text, tables, and forms.

2. **Creates a Knowledge Base:** Feeds the extracted information, along with structured data from FAA systems, into an Amazon Bedrock Knowledge Base.

3. **Enables Natural Language Querying:** Deploys a secure web application where authenticated users can interact with an Amazon Bedrock Agent, asking complex questions and receiving comprehensive answers synthesized from all underlying data sources.

## 4. High-Level Architecture

The system follows a modern, event-driven architecture on AWS. Data is ingested into S3, processed via a series of Lambda functions and AWS Glue jobs, and then indexed into a Bedrock Knowledge Base. A Streamlit application, hosted on ECS and fronted by CloudFront, provides the secure user interface.

### PDF Processing Pipeline

PDF technical manuals undergo preprocessing to extract searchable text and optimize file sizes. Large PDFs (900+ pages) that exceed Lambda's 15-minute timeout are handled through an asynchronous compression workflow using AWS Step Functions and ECS Fargate, ensuring reliable processing of documents of any size without timeouts.

## 5. Technology Stack

This project leverages a suite of modern AWS services, including:

* **AI / ML:** Amazon Bedrock (Knowledge Bases, Agents, LLMs), Amazon Textract

* **Compute:** AWS Lambda, Amazon ECS (with Fargate), AWS Step Functions

* **Storage:** Amazon S3

* **Database / Analytics:** AWS Glue, Amazon Athena

* **Application & Networking:** Amazon CloudFront, Application Load Balancer, Amazon Cognito

* **Front-End:** Streamlit

* **Infrastructure as Code:** AWS CDK / CloudFormation (TBD)

* **CI/CD:** GitHub Actions / AWS CodePipeline (TBD)

## 6. Proof of Concept Goals

The primary goals of this PoC are to:

1. **Demonstrate Feasibility:** Prove that we can successfully extract structured data from unstructured PDF manuals.

2. **Validate Integration:** Show that this extracted data can be effectively merged with data from core FAA financial and supply chain systems.

3. **Showcase User Value:** Build a functional prototype that allows users to ask complex, natural language questions and receive accurate, synthesized answers.

4. **Establish a Scalable Pattern:** Create a reference architecture that can be expanded and hardened for future production use.

## 7. Project Management

This project's work is being tracked and managed in our [**Changeis Jira Cloud**](https://changeis.atlassian.net/jira/software/projects/COMPASS/boards/103) instance. All epics, user stories, and tasks are documented there to ensure the team is aligned and progress is visible to all stakeholders.
