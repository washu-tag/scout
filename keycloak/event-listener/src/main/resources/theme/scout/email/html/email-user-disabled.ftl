<#import "template.ftl" as layout>
<@layout.emailLayout>
${kcSanitize(msg("emailUserDisabledBodyHtml"))?no_esc}
</@layout.emailLayout>